"""Orchard CLI — query the semantic graph from the command line.

Usage::

    orchard find_callers --usi s:myFunc --target MyTarget [--db ~/.orchard/graph.db]
    orchard find_callees --usi s:myFunc --target MyTarget
    orchard impact --usi s:myFunc --target MyTarget
    orchard symbol  --usi s:myFunc --target MyTarget
    orchard hierarchy --usi s:myFunc --target MyTarget
    orchard ingest  --index-store <path> [--source-root <dir>]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _find_project_db() -> str | None:
    """Walk up from cwd to find ``.orchard/graph.db`` (GitNexus-style)."""
    found = _find_project_db_with_origin()
    return found[0] if found else None


def _find_project_db_with_origin() -> tuple[str, bool] | None:
    """Return ``(db_path, from_parent_directory)`` when a project DB is found."""
    cwd = Path.cwd().resolve()
    for directory in [cwd, *cwd.parents]:
        db = directory / ".orchard" / "graph.db"
        if db.exists():
            return str(db), directory != cwd
    return None


def _conn(db_path: str = "", announce_parent: bool = False):
    from orchard.graph.db import get_connection, init_schema
    path = db_path or os.environ.get("ORCHARD_DB_PATH", "")
    if not path:
        discovered = _find_project_db_with_origin()
        if discovered:
            path, from_parent = discovered
            if from_parent:
                stream = sys.stdout if announce_parent else sys.stderr
                print(f"Using database at {path} (found in parent directory)", file=stream)
    if not path:
        path = os.path.expanduser("~/.orchard/graph.db")
    c = get_connection(path)
    init_schema(c)
    return c


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _latest_build_snapshot(conn, target_id: str = "") -> dict[str, str] | None:
    if target_id:
        rows = conn.execute(
            "MATCH (b:BuildSnapshot)-[:BuiltTarget]->(t:Target {id: $target_id}) "
            "RETURN b.id, b.created_at, b.commit_sha, b.index_store_path, b.sdk, b.configuration "
            "ORDER BY b.created_at DESC LIMIT 1",
            {"target_id": target_id},
        ).get_all()
    else:
        rows = conn.execute(
            "MATCH (b:BuildSnapshot) "
            "RETURN b.id, b.created_at, b.commit_sha, b.index_store_path, b.sdk, b.configuration "
            "ORDER BY b.created_at DESC LIMIT 1"
        ).get_all()
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row[0] or "",
        "created_at": row[1] or "",
        "commit_sha": row[2] or "",
        "index_store_path": row[3] or "",
        "sdk": row[4] or "",
        "configuration": row[5] or "",
    }


def _default_build_id(conn, target_id: str = "") -> str | None:
    snapshot = _latest_build_snapshot(conn, target_id)
    return snapshot["id"] if snapshot else None


def cmd_find_callers(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.callers import CallerRequest, find_callers
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = find_callers(conn, CallerRequest(usr=usr, target_id=target, build_id=build_id))
    _print_json(r.__dict__)
    conn.close()


def cmd_find_callees(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.callees import CalleeRequest, find_callees
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = find_callees(conn, CalleeRequest(usr=usr, target_id=target, build_id=build_id))
    _print_json(r.__dict__)
    conn.close()


def cmd_impact(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.impact import ImpactRequest, impact_analysis
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = impact_analysis(conn, ImpactRequest(usr=usr, target_id=target, max_depth=5, build_id=build_id))
    _print_json(r.__dict__)
    conn.close()


def cmd_symbol(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.symbol_context import SymbolContextRequest, get_symbol_context
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = get_symbol_context(conn, SymbolContextRequest(usr=usr, target_id=target, build_id=build_id))
    _print_json(r.__dict__)
    conn.close()


def cmd_hierarchy(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.type_hierarchy import TypeHierarchyRequest, get_type_hierarchy
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = get_type_hierarchy(conn, TypeHierarchyRequest(usr=usr, target_id=target, build_id=build_id))
    _print_json(r.__dict__)
    conn.close()


def cmd_ingest(args: list[str]):
    import argparse
    ap = argparse.ArgumentParser(prog="orchard ingest")
    ap.add_argument("--index-store", default="",
                    help="Path to IndexStore/DataStore (auto-detected if omitted)")
    ap.add_argument("--project-dir", default=os.getcwd(),
                    help="Xcode project directory for auto-detection (default: cwd)")
    ap.add_argument("--source-root", default="",
                    help="Only emit symbols under this directory")
    ap.add_argument("--target", default="",
                    help="Build target identifier (auto-detected from project name)")
    ap.add_argument("--db", default="",
                    help="Graph database path (default: <project>/.orchard/graph.db)")
    ns = ap.parse_args(args)
    from orchard.ingest.indexstore import read_index_store
    from orchard.normalize.identity import upsert_symbols, upsert_calls, upsert_indexstore_rels
    from orchard.ingest.symbolgraph import SymbolRecord
    from orchard.pipeline.runner import _map_indexstore_kind
    from pathlib import Path
    from orchard.build.xcode_settings import find_xcode_project, match_derived_data, get_derived_data_path

    index_store = ns.index_store
    source_root = str(Path(ns.source_root).resolve()) if ns.source_root else None

    # Auto-detect IndexStore from Xcode project when --index-store is omitted.
    if not index_store:
        project = find_xcode_project(ns.project_dir)
        if project is None:
            print("error: no --index-store given and no .xcodeproj/.xcworkspace found "
                  "from current directory", file=sys.stderr)
            sys.exit(2)
        candidates = match_derived_data(project)
        if not candidates:
            project_name = Path(project).stem
            dd_root = get_derived_data_path() or "~/Library/Developer/Xcode/DerivedData"
            print(f"error: no DerivedData found for project '{project}'.", file=sys.stderr)
            print(f"  Looked in:   {dd_root}", file=sys.stderr)
            print(f"  Pattern:     {project_name}-*/Index.noindex/DataStore", file=sys.stderr)
            print(f"  Checked:     info.plist WorkspacePath == '{project}'", file=sys.stderr)
            print(f"  Hint: Run an Xcode build (Cmd+B) on this project first,", file=sys.stderr)
            print(f"        or pass --index-store <path> to skip auto-detection.", file=sys.stderr)
            sys.exit(2)
        if not ns.db:
            ns.db = str(Path(project).parent / ".orchard" / "graph.db")
        # Use the most recently accessed candidate.
        dd_dir, index_store, _ = candidates[0]
        target = ns.target or Path(project).stem  # auto-detect from project name
        if not source_root:
            source_root = str(Path(ns.project_dir).resolve())
        print(f"auto-detected: --index-store {index_store}")
        print(f"auto-detected: --target {target}")
        if source_root:
            print(f"auto-detected: --source-root {source_root}")
        if len(candidates) > 1:
            print(f"note: {len(candidates)} matching DerivedData dirs found, using newest:")
            for dd, _, acc in candidates[:3]:
                print(f"  {dd}  (accessed {acc})")
    elif not ns.db:
        ns.db = str(Path(ns.project_dir) / ".orchard" / "graph.db")

    conn = _conn(ns.db)

    t0 = time.monotonic()
    r = read_index_store(index_store, ns.target, source_root=source_root)
    print(f"ingest: {r.elapsed_s}s  {len(r.symbols):,} syms  {len(r.relations):,} rels")
    syms = [SymbolRecord(usr=s.usr, precise_id="", name=s.name,
                         kind=_map_indexstore_kind(s.symbol_kind),
                         module=s.module or ns.target, language=s.language,
                         file_path=s.file_path or "", signature="", access_level="public",
                         container_usr=None) for s in r.symbols]
    upsert_symbols(conn, syms, ns.target); print("symbols done")
    upsert_calls(conn, r.relations, ns.target, source="indexstore", build_id="cli")
    print("calls done")
    upsert_indexstore_rels(conn, r.relations, ns.target, source="indexstore", build_id="cli")
    print(f"done  {time.monotonic()-t0:.0f}s")


def cmd_search(args: list[str]):
    """Search symbols by name pattern (regex, case-sensitive)."""
    import argparse
    ap = argparse.ArgumentParser(prog="orchard search")
    ap.add_argument("--name", required=True, help="Regex pattern for symbol name (case-sensitive)")
    ap.add_argument("--target", default="", help="Filter by target/module")
    ap.add_argument("--kind", default="", help="Filter by kind (class, method, function, etc.)")
    ap.add_argument("--language", default="", help="Filter by language (swift, objc, c, etc.)")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--db", default="")
    ns = ap.parse_args(args)
    conn = _conn(ns.db)
    pattern = _compile_search_pattern(ns.name)
    where = ["s.name =~ $pattern"]
    params: dict = {"pattern": pattern, "limit": ns.limit}
    if ns.target:
        where.append("s.module = $target")
        params["target"] = ns.target
    if ns.kind:
        where.append("s.kind = $kind")
        params["kind"] = ns.kind
    if ns.language:
        where.append("s.language = $language")
        params["language"] = ns.language
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.language, s.module "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()
    results = [{"usr": r[0], "name": r[1], "kind": r[2], "language": r[3], "module": r[4]} for r in rows]
    _print_json({"count": len(results), "results": results})
    conn.close()


def cmd_pipe(args: list[str]):
    """Execute multiple queries from stdin (JSONL) in a single process.

    Reads one JSON object per line from stdin.  Each object must have ``"cmd"``
    (one of: search, find_callers, find_callees, impact, symbol, hierarchy)
    and ``"args"`` (a dict of keyword arguments).

    Example stdin::

        {"cmd":"search","args":{"name":"initWithProvider","target":"YourModule"}}
        {"cmd":"find_callers","args":{"usr":"c:objc...(im)initWithProvider:","target_id":"YourModule"}}
        {"cmd":"find_callees","args":{"usr":"c:objc...(im)initWithProvider:","target_id":"YourModule"}}

    Results are written as JSONL to stdout (one line per input).
    Errors are caught per-line — one bad query won't kill the session.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="orchard pipe")
    ap.add_argument("--db", default="")
    ns = ap.parse_args(args)
    conn = _conn(ns.db)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"invalid JSON: {e}", "line": line[:120]}), flush=True)
            continue
        cmd = obj.get("cmd", "")
        try:
            result = _execute_pipe_cmd(conn, cmd, obj.get("args", {}))
            print(json.dumps({"cmd": cmd, "ok": True, "data": result}, default=str), flush=True)
        except Exception as e:
            print(json.dumps({"cmd": cmd, "ok": False, "error": str(e)}, default=str), flush=True)
    conn.close()


def _execute_pipe_cmd(conn, cmd: str, args: dict):
    """Dispatch a single pipe command. Handler imports are lazy."""
    req_keys = {"usr", "target_id"}

    if cmd == "search":
        return _pipe_search(conn, args)

    if cmd == "find_callers":
        from orchard.handlers.callers import CallerRequest, find_callers
        return find_callers(conn, CallerRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", "")
        )).__dict__

    if cmd == "find_callees":
        from orchard.handlers.callees import CalleeRequest, find_callees
        return find_callees(conn, CalleeRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", "")
        )).__dict__

    if cmd == "impact":
        from orchard.handlers.impact import ImpactRequest, impact_analysis
        return impact_analysis(conn, ImpactRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", ""),
            max_depth=args.get("max_depth", 5),
        )).__dict__

    if cmd == "symbol":
        from orchard.handlers.symbol_context import SymbolContextRequest, get_symbol_context
        return get_symbol_context(conn, SymbolContextRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", "")
        )).__dict__

    if cmd == "hierarchy":
        from orchard.handlers.type_hierarchy import TypeHierarchyRequest, get_type_hierarchy
        return get_type_hierarchy(conn, TypeHierarchyRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", "")
        )).__dict__

    raise ValueError(f"unknown pipe command: {cmd}")


def _compile_search_pattern(raw: str) -> str:
    """Convert a user-friendly search string to a Cypher regex.

    If *raw* already contains regex metacharacters (``.*+?^$[](){}|\\``),
    it is used as-is.  Otherwise it is wrapped in ``.*`` for substring matching.
    """
    import re as _re
    if _re.search(r'[.*+?^$\[\](){}\\|]', raw):
        return raw
    return f".*{raw}.*"


def _pipe_search(conn, args: dict):
    """Direct search query — no handler overhead for this path."""
    pattern = _compile_search_pattern(args.get("name", ""))
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
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.language, s.module "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()
    return [{"usr": r[0], "name": r[1], "kind": r[2], "language": r[3], "module": r[4]} for r in rows]


def cmd_stats(args: list[str]):
    from orchard.validation.freshness import freshness_for
    db = _parse_db(args)
    conn = _conn(db, announce_parent=True)
    print(f"Database: {db or os.environ.get('ORCHARD_DB_PATH', _find_project_db() or os.path.expanduser('~/.orchard/graph.db'))}")
    snapshot = _latest_build_snapshot(conn)
    if snapshot:
        _, freshness = freshness_for(conn, snapshot["id"], {})
        print(f"Build ID: {snapshot['id']}")
        print(f"Created At: {snapshot['created_at']}")
        print(f"Commit: {snapshot['commit_sha']}")
        print(f"IndexStore: {snapshot['index_store_path']}")
        print(f"SDK: {snapshot['sdk']}")
        print(f"Configuration: {snapshot['configuration']}")
        print(f"Freshness: {freshness}")
    for e in ["Symbol", "Calls", "Contains", "Inherits", "Implements", "Extends"]:
        n = conn.execute(f"MATCH ()-[r:{e}]->() RETURN count(r)" if e != "Symbol"
                         else "MATCH (s:Symbol) RETURN count(s)").get_all()[0][0]
        print(f"{e}: {n:,}")
    conn.close()


# ---------------------------------------------------------------------------

def _parse_common(args: list[str]) -> tuple[str, str, str]:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--usr", required=True)
    ap.add_argument("--target", default="")
    ap.add_argument("--db", default="")
    ns = ap.parse_args(args)
    return ns.usr, ns.target, ns.db


def _parse_db(args: list[str]) -> str:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    return ap.parse_args(args).db


COMMANDS = {
    "find_callers": cmd_find_callers,
    "find_callees": cmd_find_callees,
    "impact": cmd_impact,
    "symbol": cmd_symbol,
    "hierarchy": cmd_hierarchy,
    "search": cmd_search,
    "pipe": cmd_pipe,
    "ingest": cmd_ingest,
    "stats": cmd_stats,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("orchard <command> [args]")
        print("commands:", ", ".join(COMMANDS))
        return
    cmd = sys.argv[1]
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f"unknown command: {cmd}\ncommands: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(2)
    fn(sys.argv[2:])


if __name__ == "__main__":
    main()
