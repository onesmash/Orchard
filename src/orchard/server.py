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
import sys
from contextlib import asynccontextmanager

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ---------------------------------------------------------------------------
# Shared state — the DB connection lives for the server's lifetime
# ---------------------------------------------------------------------------

_DB_PATH: str = ""
"""The database path configured at startup via --db or ORCHARD_DB_PATH."""

_conn = None
"""Ladybug connection opened once at startup, reused for every tool call."""


def _get_conn():
    """Return the session-scoped connection, opening it on first access."""
    global _conn
    if _conn is None:
        from orchard.graph.db import get_connection
        from orchard.cli import _find_project_db
        path = _DB_PATH or os.environ.get("ORCHARD_DB_PATH", "")
        if not path:
            path = _find_project_db() or ""
        if not path:
            path = os.path.expanduser("~/.orchard/graph.db")
        _conn = get_connection(path)
    return _conn


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="orchard_search",
        description="Search for symbols by name (substring match) or find all methods of a class. When class_name is provided, finds matching classes and lists their methods. Returns USR, name, kind, language, and module for each match.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Substring to search for in symbol names (case-sensitive). Use this OR class_name."},
                "class_name": {"type": "string", "description": "Search for a class/struct/enum/protocol by name and list all its methods."},
                "target": {"type": "string", "description": "Filter by module/target name (e.g. TheModuleName)"},
                "kind": {"type": "string", "description": "Filter by symbol kind (class, method, function, etc.). In class mode, filters returned methods."},
                "language": {"type": "string", "description": "Filter by language (swift, objc, c)"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    ),
    Tool(
        name="orchard_find_callers",
        description="Find all callers of a symbol. Returns caller USR, name, kind, language, module, and containing owner.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR (Unified Symbol Resolution) of the target symbol"},
                "target_id": {"type": "string", "description": "Build target for disambiguation (e.g. TheModuleName)"},
                "depth": {"type": "integer", "description": "Multi-hop traversal depth (default 1, direct only)"},
                "relation_types": {"type": "string", "description": "Comma-separated edge types to traverse (default: Calls). Example: 'Calls,Inherits,Implements'"},
                "include_noise": {"type": "boolean", "description": "When false (default), filter out C++ operator overloads, logging macros, and stream helpers"},
            },
            "required": ["usr", "target_id"],
        },
    ),
    Tool(
        name="orchard_find_callees",
        description="Find all callees (symbols called by) a given symbol.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the source symbol"},
                "target_id": {"type": "string", "description": "Build target (e.g. TheModuleName)"},
                "depth": {"type": "integer", "description": "Multi-hop traversal depth (default 1, direct only)"},
                "relation_types": {"type": "string", "description": "Comma-separated edge types to traverse (default: Calls). Example: 'Calls,Inherits,Implements'"},
                "include_noise": {"type": "boolean", "description": "When false (default), filter out C++ operator overloads, logging macros, and stream helpers"},
            },
            "required": ["usr", "target_id"],
        },
    ),
    Tool(
        name="orchard_impact",
        description="Blast-radius analysis: finds all dependents grouped by depth (d1=direct, d2=indirect, ...). Returns risk level (low/medium/high/critical) and per-depth symbol lists.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol to analyze"},
                "target_id": {"type": "string", "description": "Build target (e.g. TheModuleName)"},
                "max_depth": {"type": "integer", "description": "Max traversal depth (default 5)"},
            },
            "required": ["usr", "target_id"],
        },
    ),
    Tool(
        name="orchard_symbol",
        description="Get metadata for a single symbol: name, kind, language, module, file_path, signature, access_level.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol"},
                "target_id": {"type": "string", "description": "Build target (e.g. TheModuleName)"},
            },
            "required": ["usr", "target_id"],
        },
    ),
    Tool(
        name="orchard_hierarchy",
        description="Type hierarchy for a symbol: superclasses, protocols, and subclasses/conformers.",
        inputSchema={
            "type": "object",
            "properties": {
                "usr": {"type": "string", "description": "USR of the symbol"},
                "target_id": {"type": "string", "description": "Build target (e.g. TheModuleName)"},
            },
            "required": ["usr", "target_id"],
        },
    ),
    Tool(
        name="orchard_stats",
        description="Database statistics: counts of Symbol, Calls, Contains, Inherits, Implements, and Extends edges.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="orchard_audit",
        description="Module coverage report: per-module symbol counts by kind. When project_dir is given, compares graph modules against Xcode workspace targets and flags gaps (< 100 symbols for a framework target).",
        inputSchema={
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Xcode project directory for target discovery (optional)"},
                "format": {"type": "string", "description": "Output format: table or json (default table)"},
            },
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
    import re as _re
    raw = args.get("name", "")
    if _re.search(r'[.*+?^$\[\](){}\\|]', raw):
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

    conn = _get_conn()
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.language, s.module "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()
    results = [{"usr": r[0], "name": r[1], "kind": r[2], "language": r[3], "module": r[4]} for r in rows]
    return json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2)


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
        methods = gl.methods_of(r[0], target)
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


def _do_handler(module_name: str, attr: str, request_cls_name: str, args: dict, include_noise: bool = True, depth: int = 1, relation_types: list[str] | None = None) -> str:
    """Generic dispatch: import → build request → call handler → noise filter → JSON."""
    import importlib
    mod = importlib.import_module(f"orchard.handlers.{module_name}")
    fn = getattr(mod, attr)
    cls = getattr(mod, request_cls_name)
    req = cls(
        usr=args.get("usr", ""),
        target_id=args.get("target_id", ""),
        depth=args.get("depth", depth),
        max_depth=args.get("max_depth", 5),
        relation_types=relation_types or args.get("relation_types", ["Calls"]),
    )
    conn = _get_conn()
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


HANDLERS: dict[str, callable] = {
    "orchard_search": _do_search,
    "orchard_find_callers": lambda a: _do_handler("callers", "find_callers", "CallerRequest", a, include_noise=a.get("include_noise", False), depth=a.get("depth", 1), relation_types=a.get("relation_types", "Calls").split(",") if isinstance(a.get("relation_types"), str) else ["Calls"]),
    "orchard_find_callees": lambda a: _do_handler("callees", "find_callees", "CalleeRequest", a, include_noise=a.get("include_noise", False), depth=a.get("depth", 1), relation_types=a.get("relation_types", "Calls").split(",") if isinstance(a.get("relation_types"), str) else ["Calls"]),
    "orchard_impact": lambda a: _do_handler("impact", "impact_analysis", "ImpactRequest", a),
    "orchard_symbol": lambda a: _do_handler("symbol_context", "get_symbol_context", "SymbolContextRequest", a),
    "orchard_hierarchy": lambda a: _do_handler("type_hierarchy", "get_type_hierarchy", "TypeHierarchyRequest", a),
    "orchard_stats": _do_stats,
    "orchard_audit": _do_audit,
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
