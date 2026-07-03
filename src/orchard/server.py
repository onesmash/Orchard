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
from pathlib import Path
from dataclasses import dataclass
from contextlib import asynccontextmanager
from contextlib import suppress
from urllib.parse import unquote, urlparse

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
try:
    from watchfiles import awatch
except ModuleNotFoundError:  # pragma: no cover - exercised in constrained test envs
    async def awatch(*_args, **_kwargs):
        if False:
            yield set()

from orchard.logging import get_orchard_logger
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
_conn_db_path: str = ""
"""Resolved DB path for the currently cached connection."""
_conn_by_db_path: dict[str, object] = {}
"""Ladybug connections keyed by resolved DB path."""
_startup_ingest_task: asyncio.Task | None = None
"""Most recently scheduled background bootstrap task."""
_startup_ingest_tasks: dict[str, asyncio.Task] = {}
"""Background bootstrap tasks keyed by project root."""
_ingest_state_watch_task: asyncio.Task | None = None
"""Background ingest-state watcher task, if one is currently running."""

_SERVER_LOGGER = get_orchard_logger("server", console=True)


@dataclass(frozen=True)
class ResolvedTarget:
    project_dir: str | None
    db_path: str
    source: str
    watcher_eligible: bool


def _resolve_target(project_dir: str | None, source_hint: str = "request_root") -> ResolvedTarget:
    """Resolve the request target before any connection or watcher side effects."""
    normalized_project_dir = str(Path(project_dir).expanduser().resolve()) if project_dir else None
    if normalized_project_dir:
        candidate = (Path(normalized_project_dir) / ".orchard" / "graph.db").resolve()
        if candidate.exists():
            return ResolvedTarget(
                project_dir=normalized_project_dir,
                db_path=str(candidate),
                source=source_hint,
                watcher_eligible=True,
            )

    if _DB_PATH:
        return ResolvedTarget(
            project_dir=None,
            db_path=str(Path(_DB_PATH).expanduser().resolve()),
            source="cli_db",
            watcher_eligible=False,
        )

    env_path = os.environ.get("ORCHARD_DB_PATH", "")
    if env_path:
        return ResolvedTarget(
            project_dir=None,
            db_path=str(Path(env_path).expanduser().resolve()),
            source="env_db",
            watcher_eligible=False,
        )

    raise RuntimeError(
        "No Orchard graph database configured. "
        "Provide a request workspace root with .orchard/graph.db, "
        "or pass --db / set ORCHARD_DB_PATH."
    )


async def _resolve_request_target(project_dir: str | None = None) -> ResolvedTarget:
    """Resolve the active request target before any side effects."""
    if project_dir:
        return _resolve_target(project_dir, source_hint="tool_project_dir")
    return _resolve_target(await _request_project_dir())


def _resolve_db_path(project_dir: str | None) -> str:
    """Resolve the graph DB path for the active project context."""
    return _resolve_target(project_dir).db_path


def _translate_missing_read_only_db_error(exc: Exception) -> str | None:
    if "Cannot create an empty database under READ ONLY mode." not in str(exc):
        return None
    from orchard.cli import _resolve_read_only_db_path

    path = _resolve_read_only_db_path(_DB_PATH)
    return (
        f"database not found: {path}. "
        "Run `orchard ingest --project-dir .` first, "
        "or pass --db <path> / set ORCHARD_DB_PATH."
    )


def _background_project_dir() -> str | None:
    """Return a safe project root for background ingest/watch tasks."""
    configured = _DB_PATH or os.environ.get("ORCHARD_DB_PATH", "")
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.name == "graph.db" and path.parent.name == ".orchard":
            return str(path.parent.parent)
        if path.is_dir():
            return str(path)

    cwd = Path.cwd().resolve()
    if cwd == cwd.parent:
        return None
    return str(cwd)


async def _request_project_dir() -> str | None:
    """Return the first MCP workspace root for the active request, if any."""
    try:
        roots = await app.request_context.session.list_roots()
    except Exception as exc:
        _SERVER_LOGGER.warning("[orchard-mcp] list_roots failed: %s", exc)
        return None

    root_uris = [str(root.uri) for root in roots.roots]
    _SERVER_LOGGER.info("[orchard-mcp] request roots=%s", root_uris)
    for root in roots.roots:
        parsed = urlparse(str(root.uri))
        if parsed.scheme != "file":
            continue
        path = Path(unquote(parsed.path)).resolve()
        _SERVER_LOGGER.info("[orchard-mcp] selected request root=%s", path)
        return str(path)
    _SERVER_LOGGER.warning("[orchard-mcp] no file:// roots available in request")
    return None


def _reset_conn(reason: str) -> None:
    """Close the cached DB connection so the next request reopens fresh state."""
    global _conn, _conn_db_path, _conn_by_db_path
    if _conn is None and not _conn_by_db_path:
        return
    for conn in list(_conn_by_db_path.values()):
        conn.close()
    _conn_by_db_path.clear()
    if _conn is not None:
        _conn.close()
    _conn = None
    _conn_db_path = ""
    _SERVER_LOGGER.info("[orchard-mcp] DB connection reset after %s", reason)


async def _watch_ingest_state(project_dir: str) -> None:
    """Watch ingest-state.json and reset the cached DB connection on changes."""
    state_path = (Path(project_dir) / ".orchard" / "ingest-state.json").resolve()

    def _watch_filter(_change, changed_path: str) -> bool:
        return Path(changed_path).resolve() == state_path

    async for _changes in awatch(str(project_dir), watch_filter=_watch_filter):
        _SERVER_LOGGER.info("[orchard-mcp] ingest-state changed path=%s", state_path)
        _reset_conn("ingest-state update")


async def _run_startup_ingest(project_dir: str) -> None:
    """Best-effort background ingest in the current project directory."""
    from orchard.ingest.indexstore import _orchard_cli_path

    cli_path = _orchard_cli_path()
    proc = await asyncio.create_subprocess_exec(
        cli_path,
        "ingest",
        "--project-dir",
        project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_data, stderr_data = await proc.communicate()
    stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
    if proc.returncode == 0:
        _SERVER_LOGGER.info("[orchard-mcp] startup ingest finished project_dir=%s", project_dir)
        if stdout_text:
            for line in stdout_text.splitlines():
                _SERVER_LOGGER.info("%s", line)
        if stderr_text:
            for line in stderr_text.splitlines():
                _SERVER_LOGGER.info("%s", line)
        _reset_conn("startup ingest")
        return

    _SERVER_LOGGER.error(
        "[orchard-mcp] startup ingest failed project_dir=%s exit=%s",
        project_dir,
        proc.returncode,
    )
    if stdout_text:
        for line in stdout_text.splitlines():
            _SERVER_LOGGER.error("%s", line)
    if stderr_text:
        for line in stderr_text.splitlines():
            _SERVER_LOGGER.error("%s", line)


async def _bootstrap_project_ingest(project_dir: str) -> None:
    """Ensure indexd is running for a project before falling back to ingest."""
    from orchard.ingest.indexstore import indexd_status

    try:
        status = await asyncio.to_thread(indexd_status)
    except Exception as exc:
        _SERVER_LOGGER.warning(
            "[orchard-mcp] indexd status probe failed project_dir=%s error=%s",
            project_dir,
            exc,
        )
        status = {"running": False}

    if status.get("running"):
        _SERVER_LOGGER.info("[orchard-mcp] indexd already running project_dir=%s", project_dir)
        return

    await _run_startup_ingest(project_dir)


def _track_startup_ingest_task(project_dir: str, task: asyncio.Task) -> None:
    """Remember one bootstrap task per project and clean up on completion."""
    global _startup_ingest_task
    _startup_ingest_task = task
    _startup_ingest_tasks[project_dir] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        current = _startup_ingest_tasks.get(project_dir)
        if current is done_task:
            _startup_ingest_tasks.pop(project_dir, None)

    task.add_done_callback(_cleanup)


def _schedule_tool_ingest_if_needed(project_dir: str | None) -> None:
    """Schedule one non-blocking bootstrap task per project root."""
    if not project_dir:
        return
    existing = _startup_ingest_tasks.get(project_dir)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_bootstrap_project_ingest(project_dir))
    _track_startup_ingest_task(project_dir, task)


def _get_conn(project_dir: str | None = None):
    """Return the session-scoped connection, opening it on first access.

    Opens the database in read-only mode so multiple MCP server instances
    (e.g. from different editor windows) can share the same database.
    """
    global _conn, _conn_db_path, _conn_by_db_path
    if project_dir is None and _conn is not None:
        return _conn
    if project_dir is None and len(_conn_by_db_path) == 1:
        path, conn = next(iter(_conn_by_db_path.items()))
        _conn = conn
        _conn_db_path = path
        return _conn
    path = _resolve_db_path(project_dir)
    if path in _conn_by_db_path:
        _conn = _conn_by_db_path[path]
        _conn_db_path = path
        return _conn
    if _conn is None or _conn_db_path != path:
        from orchard.graph.db import get_connection
        conn = get_connection(path, read_only=True)
        _conn_by_db_path[path] = conn
        _conn = conn
        _conn_db_path = path
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


def _conn_for_args(args: dict | None = None):
    """Resolve a connection using explicit tool context when provided."""
    project_dir = (args or {}).get("project_dir")
    return _get_conn(project_dir if project_dir else None)


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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
            },
        },
    ),
    Tool(
        name="orchard_target_action_graph",
        description="Query the UIKit target-action graph. Returns bindings grouped by callback (default) or registrar, including selector, source file, line, and raw control event token.",
        inputSchema={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Filter by selector name such as onToggle:."},
                "callback_usr": {"type": "string", "description": "Filter by callback USR."},
                "file": {"type": "string", "description": "Filter by registrar file path substring."},
                "group_by": {"type": "string", "description": "Grouping mode: 'callback' (default) or 'registrar'."},
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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
                "project_dir": {"type": "string", "description": "Optional project root directory. When provided, Orchard resolves <project_dir>/.orchard/graph.db directly instead of relying on MCP roots."},
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

    conn = _conn_for_args(args)
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

    conn = _conn_for_args(args)
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
    conn = _conn_for_args(args)
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
    conn = _conn_for_args(args)
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


def _do_stats(args: dict) -> str:
    conn = _conn_for_args(args)
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

    conn = _conn_for_args(args)
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
    conn = _conn_for_args(args)
    build_id = args.get("build_id") or _default_build_id_safe(conn, "")
    req = cls(
        notification_name=args.get("notification_name", ""),
        group_by=args.get("group_by", "notification"),
        build_id=build_id,
    )
    result = fn(conn, req)
    return json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str)


def _do_target_action_graph(args: dict) -> str:
    """Query UIKit target-action graph."""
    import importlib
    mod = importlib.import_module("orchard.handlers.target_action_graph")
    cls = getattr(mod, "TargetActionGraphRequest")
    fn = getattr(mod, "get_target_action_graph")
    conn = _get_conn()
    build_id = args.get("build_id") or _default_build_id_safe(conn, "")
    req = cls(
        selector=args.get("selector", ""),
        callback_usr=args.get("callback_usr", ""),
        file=args.get("file", ""),
        group_by=args.get("group_by", "callback"),
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
    "orchard_target_action_graph": _do_target_action_graph,
}


# ---------------------------------------------------------------------------
# MCP server boilerplate
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(server: Server):
    """Called once at startup. Runtime side effects are request-driven."""
    global _startup_ingest_task, _ingest_state_watch_task
    try:
        yield
    finally:
        global _conn
        for task in list(_startup_ingest_tasks.values()):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        _startup_ingest_tasks.clear()
        if _startup_ingest_task is not None and not _startup_ingest_task.done():
            _startup_ingest_task.cancel()
            with suppress(asyncio.CancelledError):
                await _startup_ingest_task
        _startup_ingest_task = None
        if _ingest_state_watch_task is not None and not _ingest_state_watch_task.done():
            _ingest_state_watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await _ingest_state_watch_task
        _ingest_state_watch_task = None
        _reset_conn("shutdown")


app = Server("orchard-mcp", version="0.2.0", lifespan=_lifespan)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = HANDLERS.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    target = None
    try:
        project_dir = arguments.get("project_dir") if isinstance(arguments, dict) else None
        target = await _resolve_request_target(project_dir)
        _SERVER_LOGGER.info(
            "[orchard-mcp] tool=%s target_source=%s project_dir=%s db_path=%s",
            name,
            target.source,
            target.project_dir,
            target.db_path,
        )
        if target.watcher_eligible:
            _schedule_tool_ingest_if_needed(target.project_dir)
    except Exception as exc:
        _SERVER_LOGGER.error("[orchard-mcp] background ingest scheduling failed: %s", exc)
    try:
        _get_conn(target.project_dir if target else None)
        result = await asyncio.to_thread(handler, arguments)
    except Exception as exc:
        translated = _translate_missing_read_only_db_error(exc)
        return [TextContent(
            type="text",
            text=json.dumps({"error": translated or str(exc)}, ensure_ascii=False),
        )]
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
