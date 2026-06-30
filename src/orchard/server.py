"""Orchard MCP server — keeps a single DB connection alive for the session.

Uses stdio transport (stdin/stdout JSON-RPC).  This is a thin adapter: every
tool handler delegates to the existing ``orchard.handlers.*`` functions.

Start with::

    uv run orchard-mcp [--db /path/to/graph.db]

The server is meant to be launched by Claude Code / Claude Desktop as a
long-running subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from orchard.query.annotations import annotate_symbol_source_scope
from orchard.query.search_contract import SearchResponse, SearchStatus
from orchard.query.frame_lookup import lookup_frame
from orchard.query.search_planner import (
    classify_search_query,
    plan_search_next_actions,
    rank_symbol_candidates,
)
from orchard.validation.freshness import freshness_for, map_search_freshness


# ---------------------------------------------------------------------------
# Shared state — the DB connection lives for the server's lifetime
# ---------------------------------------------------------------------------

_DB_PATH: str = ""
"""The database path configured at startup via --db or ORCHARD_DB_PATH."""

_conn = None
"""Ladybug connection opened once at startup, reused for every tool call."""


def _get_conn():
    """Return the session-scoped connection, opening it on first access.

    Opens the database in read-only mode so multiple MCP server instances
    (e.g. from different editor windows) can share the same database.
    """
    global _conn
    if _conn is None:
        from orchard.graph.db import get_connection
        from orchard.cli import _find_project_db
        path = _DB_PATH or os.environ.get("ORCHARD_DB_PATH", "")
        if not path:
            path = _find_project_db() or ""
        if not path:
            path = os.path.expanduser("~/.orchard/graph.db")
        _conn = get_connection(path, read_only=True)
    return _conn


def _default_build_id_safe(conn, scope_id: str = "") -> str | None:
    """Return the latest build snapshot ID, or None if none exists.

    Wraps ``cli._default_build_id`` with error handling so that freshness
    resolution never crashes a handler.
    """
    try:
        from orchard.cli import _default_build_id
        return _default_build_id(conn, scope_id)
    except Exception:
        return None


def _workspace_root_safe(conn, build_id: str | None = None) -> str:
    """Return the active workspace root, or cwd if no snapshot records one."""
    try:
        if build_id:
            rows = conn.execute(
                "MATCH (b:BuildSnapshot {id: $id}) RETURN b.workspace_root LIMIT 1",
                {"id": build_id},
            ).get_all()
            if rows and rows[0][0]:
                return rows[0][0]
        rows = conn.execute(
            "MATCH (b:BuildSnapshot) "
            "RETURN b.workspace_root ORDER BY b.created_at DESC LIMIT 1"
        ).get_all()
        if rows and rows[0][0]:
            return rows[0][0]
    except Exception:
        pass
    return os.getcwd()


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="orchard_search",
        description="Search for symbols by name or qualified name. Returns compact status, diagnostics, candidates, and next actions. If the input looks like a stack frame, use orchard_lookup_frame.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Substring to search for in symbol names (case-sensitive). Use this OR class_name."},
                "class_name": {"type": "string", "description": "Search for a class/struct/enum/protocol by name and list all its methods."},
                "target": {"type": "string", "description": "Filter by module/target name (e.g. TheModuleName)"},
                "kind": {"type": "string", "description": "Filter by symbol kind (class, method, function, etc.). In class mode, filters returned methods."},
                "language": {"type": "string", "description": "Filter by language (swift, objc, c)"},
                "file": {"type": "string", "description": "Filter by file path (substring match)"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    ),
    Tool(
        name="orchard_find_references",
        description="Find incoming and outgoing references for a symbol. Returns both callers (incoming) and callees (outgoing). Each edge includes confidence (compiler-verified/inferred) and provenance labels. ObjC callees carry semantic_role (notification_observer, delegate_setter, framework_callback...) inline — no separate tool needed.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol"},
            },
            "required": ["usr"],
        },
    ),
    Tool(
        name="orchard_find_callers",
        description="Find all callers of a symbol. Each entry includes confidence (compiler-verified/inferred) and provenance labels so you can distinguish source-level evidence from compiler-inferred edges.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR (Unified Symbol Resolution) of the target symbol"},
                "depth": {"type": "integer", "description": "Multi-hop traversal depth (default 1, direct only)"},
                "relation_types": {"type": "string", "description": "Comma-separated edge types to traverse (default: Calls). Example: 'Calls,Inherits,Implements'"},
                "include_noise": {"type": "boolean", "description": "When false (default), filter out C++ operator overloads, logging macros, and stream helpers"},
                "include_inferred": {"type": "boolean", "description": "When true, include compiler-inferred edges (reason=indexstore_relation_only). Default false: only source-level call evidence."},
            },
            "required": ["usr"],
        },
    ),
    Tool(
        name="orchard_find_callees",
        description="Find all callees (symbols called by) a given symbol. Each entry includes confidence (compiler-verified/inferred). ObjC callees carry semantic_role (notification_observer, delegate_setter, framework_callback...) inline — no separate tool needed. Set include_notification_bridges=true to annotate notification_observer callees with matching notification name, selector, and callback.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the source symbol"},
                "depth": {"type": "integer", "description": "Multi-hop traversal depth (default 1, direct only)"},
                "relation_types": {"type": "string", "description": "Comma-separated edge types to traverse (default: Calls). Example: 'Calls,Inherits,Implements'"},
                "include_noise": {"type": "boolean", "description": "When false (default), filter out C++ operator overloads, logging macros, and stream helpers"},
                "include_inferred": {"type": "boolean", "description": "When true, include compiler-inferred edges (reason=indexstore_relation_only). Default false: only source-level call evidence."},
                "include_notification_bridges": {"type": "boolean", "description": "When true, annotate notification_observer callees with notification_bridges: notification_name, selector, and callback symbol. Default false."},
            },
            "required": ["usr"],
        },
    ),
    Tool(
        name="orchard_impact",
        description="Blast-radius analysis: finds all dependents grouped by depth (d1=direct, d2=indirect, ...). Returns risk level (low/medium/high/critical) and per-depth symbol lists.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol to analyze"},
                "max_depth": {"type": "integer", "description": "Max traversal depth (default 5)"},
            },
            "required": ["usr"],
        },
    ),
    Tool(
        name="orchard_symbol",
        description="Get metadata for a single symbol: name, kind, language, module, file_path, signature, access_level.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol"},
            },
            "required": ["usr"],
        },
    ),
    Tool(
        name="orchard_hierarchy",
        description="Type hierarchy for a symbol: superclasses, protocols, and subclasses/conformers.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol"},
            },
            "required": ["usr"],
        },
    ),
    Tool(
        name="orchard_notification_graph",
        description="Query the NSNotificationCenter publisher-observer graph. Returns notifications grouped by name (default), each with posters and observers. Observers now carry identity (who registered), selector, and callback. Use group_by='observer' to pivot by observer — see each observer's registrations at a glance.",
        inputSchema={
            "type": "object",
            "properties": {
                "notification_name": {"type": "string", "description": "Filter by notification name (substring match). Omit to return all notifications."},
                "group_by": {"type": "string", "description": "Grouping mode: 'notification' (default) or 'observer' — pivots by observer USR showing each observer's registrations."},
            },
        },
    ),
    Tool(
        name="orchard_lookup_frame",
        description="Resolve a single frame-like symbol text into indexed graph context. Does not parse full crashlogs or crashed-thread blocks.",
        inputSchema={
            "type": "object",
            "properties": {
                "frame": {"type": "string", "description": "A single stack frame or crash-frame-like string."},
                "target": {"type": "string", "description": "Optional module/target filter."},
                "language": {"type": "string", "description": "Optional language filter."},
            },
            "required": ["frame"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handler dispatch
# ---------------------------------------------------------------------------

def _do_search(args: dict) -> str:
    """Search symbols by name or find class methods."""
    class_name = args.get("class_name", "")
    if class_name:
        return _do_search_class(args)
    return _do_search_name(args)


def _do_search_name(args: dict) -> str:
    """Search symbols by name — inline Cypher, no handler overhead."""
    raw = args.get("name", "")
    query_kind = classify_search_query(raw)
    if query_kind == "frame":
        response = SearchResponse(
            query={"raw": raw, "kind": query_kind},
            status=SearchStatus(
                outcome="no_match", coverage="unknown", freshness="unknown"
            ),
            matches=[],
            diag=["frame_lookup_recommended"],
            candidates={"symbols": [], "owners": [], "text": [raw]},
            next_actions=[{"tool": "orchard_lookup_frame", "args": {"frame": raw}}],
        )
        return json.dumps(response.to_dict(), ensure_ascii=False, indent=2)

    if re.search(r'[.*+?^$\[\](){}\\|]', raw):
        pattern = raw
    else:
        pattern = f".*{raw}.*"

    target = args.get("target", "")
    kind = args.get("kind", "")
    language = args.get("language", "")
    limit = args.get("limit", 20)

    where = ["s.name =~ $pattern"]
    params: dict = {"pattern": pattern, "limit": limit}
    if target:
        where.append("s.module = $target")
        params["target"] = target
    if kind:
        where.append("s.kind = $kind")
        params["kind"] = kind
    if language:
        where.append("s.language = $language")
        params["language"] = language
    if args.get("file"):
        params["file_pattern"] = f".*{args['file']}.*"
        where.append("s.file_path =~ $file_pattern")

    conn = _get_conn()
    build_id = args.get("build_id") or _default_build_id_safe(conn, target or "")
    workspace_root = _workspace_root_safe(conn, build_id)
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.language, s.module, s.file_path "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()
    matches = rank_symbol_candidates(
        raw,
        [
            annotate_symbol_source_scope(
                {
                    "usr": r[0],
                    "name": r[1],
                    "kind": r[2],
                    "language": r[3],
                    "module": r[4],
                    "file_path": r[5] or "",
                },
                workspace_root,
            )
            for r in rows
        ],
        target=target,
        language=language,
    )
    outcome = "match" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "no_match"
    coverage = "covered" if matches else "unknown"
    snapshot_status = "stale"
    if build_id:
        _, snapshot_status = freshness_for(conn, build_id, {})
    freshness = map_search_freshness(snapshot_status)
    status = SearchStatus(outcome=outcome, coverage=coverage, freshness=freshness)
    response = SearchResponse(
        query={"raw": raw, "kind": query_kind},
        status=status,
        matches=matches[:5],
        diag=([] if matches else ["text_fallback_recommended"])
        + (["index_stale"] if freshness == "stale" else []),
        candidates={
            "symbols": matches[:3],
            "owners": [],
            "text": [raw] if not matches else [],
        },
        next_actions=plan_search_next_actions(
            status,
            {"symbols": matches[:3], "owners": [], "text": [raw] if not matches else []},
            raw,
        ),
    )
    return json.dumps(response.to_dict(), ensure_ascii=False, indent=2)


def _do_search_class(args: dict) -> str:
    """Class search: find matching classes and list their methods."""
    import re as _re
    from orchard.query.lookup import GraphLookup

    raw = args["class_name"]
    if _re.search(r'[.*+?^$\[\](){}\\|]', raw):
        class_pattern = raw
    else:
        class_pattern = f".*{raw}.*"

    target = args.get("target", "")
    kind_filter = args.get("kind", "")
    limit = args.get("limit", 20)

    conn = _get_conn()
    gl = GraphLookup(conn)

    where = [
        "s.name =~ $pattern",
        "s.kind IN ['class', 'struct', 'enum', 'protocol']",
    ]
    params: dict = {"pattern": class_pattern, "limit": limit}
    if target:
        where.append("s.module = $target")
        params["target"] = target

    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.module "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()

    owners = []
    for r in rows:
        owner = {"usr": r[0], "name": r[1], "kind": r[2], "module": r[3]}
        methods = gl.methods_of(r[0])
        if kind_filter:
            methods = [m for m in methods if m["kind"] == kind_filter]
        owners.append({"owner": owner, "methods": methods})

    total_methods = 0
    for entry in owners:
        if total_methods >= limit:
            entry["methods"] = []
        elif total_methods + len(entry["methods"]) > limit:
            entry["methods"] = entry["methods"][:limit - total_methods]
        total_methods += len(entry["methods"])

    return json.dumps({
        "owners": owners,
        "total_methods": sum(len(e["methods"]) for e in owners),
    }, ensure_ascii=False, indent=2)


def _do_lookup_frame(args: dict) -> str:
    """Lookup a crash frame or frame-like string."""
    conn = _get_conn()
    freshness = _search_freshness_for_args(conn, args)
    result = lookup_frame(
        conn,
        args.get("frame", ""),
        target=args.get("target", ""),
        language=args.get("language", ""),
        freshness=freshness,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


def _search_freshness_for_args(conn, args: dict) -> str:
    target = args.get("target", "")
    build_id = args.get("build_id") or _default_build_id_safe(conn, target or "")
    snapshot_status = "unknown"
    if build_id:
        _, snapshot_status = freshness_for(conn, build_id, {})
    return map_search_freshness(snapshot_status)


def _do_handler(module_name: str, attr: str, request_cls_name: str, args: dict, include_noise: bool = True, include_inferred: bool = False, depth: int = 1, relation_types: list[str] | None = None) -> str:
    """Generic dispatch: import → build request → call handler → noise filter → JSON."""
    import importlib
    mod = importlib.import_module(f"orchard.handlers.{module_name}")
    fn = getattr(mod, attr)
    cls = getattr(mod, request_cls_name)
    conn = _get_conn()
    # Auto-resolve build_id so freshness is accurate by default.
    build_id = args.get("build_id") or _default_build_id_safe(conn, "")
    req = cls(
        usr=args.get("usr", ""),
        build_id=build_id,
        depth=args.get("depth", depth),
        max_depth=args.get("max_depth", 5),
        relation_types=relation_types or args.get("relation_types", ["Calls"]),
        include_inferred=args.get("include_inferred", include_inferred),
    )
    # Pass include_notification_bridges through if the request class supports it.
    if "include_notification_bridges" in args:
        req.include_notification_bridges = args["include_notification_bridges"]
    result = fn(conn, req)
    if not include_noise:
        from orchard.query.noise_filter import filter_noise
        filtered, removed = filter_noise(result.data)
        result.data = filtered
        result.noise_removed = removed
    return json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str)


def _do_stats(_args: dict) -> str:
    conn = _get_conn()
    lines = []
    for e in ["Symbol", "Calls", "Contains", "Inherits", "Implements", "Extends"]:
        n = conn.execute(
            f"MATCH ()-[r:{e}]->() RETURN count(r)" if e != "Symbol"
            else "MATCH (s:Symbol) RETURN count(s)"
        ).get_all()[0][0]
        lines.append(f"{e}: {n:,}")
    return "\n".join(lines)


def _do_audit(args: dict) -> str:
    """Module coverage report: symbol counts by kind, Xcode target gap detection."""
    from orchard.query.lookup import GraphLookup
    from orchard.cli import _discover_xcode_targets, _detect_gaps, _format_audit_table, ANOMALY_THRESHOLD

    conn = _get_conn()
    gl = GraphLookup(conn)
    stats = gl.module_stats()

    project_dir = args.get("project_dir", "")
    fmt = args.get("format", "table")
    xcode_targets = None
    if project_dir:
        xcode_targets = _discover_xcode_targets(project_dir)

    if fmt == "json":
        result = {
            "modules": stats,
            "xcode_targets": xcode_targets,
            "anomaly_threshold": ANOMALY_THRESHOLD,
        }
        if xcode_targets:
            result["gaps"] = _detect_gaps(stats, xcode_targets)
        return json.dumps(result, ensure_ascii=False, indent=2)
    else:
        table = _format_audit_table(stats, xcode_targets)
        total_symbols = sum(r["count"] for r in stats)
        unique_modules = len({r["module"] for r in stats})
        unique_kinds = len({r["kind"] for r in stats})
        return table + f"\n\nTotal: {total_symbols:,} symbols across {unique_modules} modules ({unique_kinds} kinds)"


def _do_notification_graph(args: dict) -> str:
    """Query NSNotificationCenter publisher-observer graph."""
    import importlib
    mod = importlib.import_module("orchard.handlers.notification_graph")
    cls = getattr(mod, "NotificationGraphRequest")
    fn = getattr(mod, "get_notification_graph")
    conn = _get_conn()
    build_id = args.get("build_id") or _default_build_id_safe(conn, "")
    req = cls(
        notification_name=args.get("notification_name", ""),
        group_by=args.get("group_by", "notification"),
        build_id=build_id,
    )
    result = fn(conn, req)
    return json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str)


HANDLERS: dict[str, callable] = {
    "orchard_search": _do_search,
    "orchard_lookup_frame": _do_lookup_frame,
    "orchard_find_references": lambda a: _do_handler("references", "find_references", "ReferencesRequest", a),
    "orchard_find_callers": lambda a: _do_handler("callers", "find_callers", "CallerRequest", a, include_noise=a.get("include_noise", False), depth=a.get("depth", 1), relation_types=a.get("relation_types", "Calls").split(",") if isinstance(a.get("relation_types"), str) else ["Calls"]),
    "orchard_find_callees": lambda a: _do_handler("callees", "find_callees", "CalleeRequest", a, include_noise=a.get("include_noise", False), depth=a.get("depth", 1), relation_types=a.get("relation_types", "Calls").split(",") if isinstance(a.get("relation_types"), str) else a.get("relation_types", ["Calls"])),
    "orchard_impact": lambda a: _do_handler("impact", "impact_analysis", "ImpactRequest", a),
    "orchard_symbol": lambda a: _do_handler("symbol_context", "get_symbol_context", "SymbolContextRequest", a),
    "orchard_hierarchy": lambda a: _do_handler("type_hierarchy", "get_type_hierarchy", "TypeHierarchyRequest", a),
    "orchard_notification_graph": _do_notification_graph,
}


# ---------------------------------------------------------------------------
# MCP server boilerplate
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(server: Server):
    """Called once at startup.  We just trigger a connection warm-up."""
    try:
        _get_conn()
        sys.stderr.write("[orchard-mcp] DB connected\n")
        sys.stderr.flush()
    except Exception as exc:
        sys.stderr.write(f"[orchard-mcp] DB connection failed: {exc}\n")
        sys.stderr.flush()
    try:
        yield
    finally:
        global _conn
        if _conn is not None:
            _conn.close()
            _conn = None


app = Server("orchard-mcp", version="0.2.0", lifespan=_lifespan)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = HANDLERS.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        result = await asyncio.to_thread(handler, arguments)
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
    return [TextContent(type="text", text=result)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Parse --db from argv, then start the stdio server loop."""
    import argparse
    ap = argparse.ArgumentParser(prog="orchard-mcp")
    ap.add_argument("--db", default="", help="Path to graph database")
    ns, _ = ap.parse_known_args()
    global _DB_PATH
    _DB_PATH = ns.db or ""

    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
