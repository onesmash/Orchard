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


def _conn(db_path: str = ""):
    from orchard.graph.db import get_connection, init_schema
    path = db_path or os.environ.get("ORCHARD_DB_PATH", "")
    if not path:
        path = os.path.expanduser("~/.orchard/graph.db")
    c = get_connection(path)
    init_schema(c)
    return c


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_find_callers(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.callers import CallerRequest, find_callers
    conn = _conn(db)
    r = find_callers(conn, CallerRequest(usr=usr, target_id=target))
    _print_json(r.__dict__)
    conn.close()


def cmd_find_callees(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.callees import CalleeRequest, find_callees
    conn = _conn(db)
    r = find_callees(conn, CalleeRequest(usr=usr, target_id=target))
    _print_json(r.__dict__)
    conn.close()


def cmd_impact(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.impact import ImpactRequest, impact_analysis
    conn = _conn(db)
    r = impact_analysis(conn, ImpactRequest(usr=usr, target_id=target, max_depth=5))
    _print_json(r.__dict__)
    conn.close()


def cmd_symbol(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.symbol_context import SymbolContextRequest, get_symbol_context
    conn = _conn(db)
    r = get_symbol_context(conn, SymbolContextRequest(usr=usr, target_id=target))
    _print_json(r.__dict__)
    conn.close()


def cmd_hierarchy(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.type_hierarchy import TypeHierarchyRequest, get_type_hierarchy
    conn = _conn(db)
    r = get_type_hierarchy(conn, TypeHierarchyRequest(usr=usr, target_id=target))
    _print_json(r.__dict__)
    conn.close()


def cmd_ingest(args: list[str]):
    import argparse
    ap = argparse.ArgumentParser(prog="orchard ingest")
    ap.add_argument("--index-store", required=True)
    ap.add_argument("--source-root", default="")
    ap.add_argument("--target", default="Zoom")
    ap.add_argument("--db", default="")
    ns = ap.parse_args(args)
    conn = _conn(ns.db)
    from orchard.ingest.indexstore import read_index_store
    from orchard.normalize.identity import upsert_symbols, upsert_calls, upsert_indexstore_rels
    from orchard.ingest.symbolgraph import SymbolRecord
    from orchard.pipeline.runner import _map_indexstore_kind
    t0 = time.monotonic()
    r = read_index_store(ns.index_store, ns.target, source_root=ns.source_root or None)
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

        {"cmd":"search","args":{"name":"initWithProvider","target":"Zoom"}}
        {"cmd":"find_callers","args":{"usr":"c:objc...(im)initWithProvider:","target_id":"Zoom"}}
        {"cmd":"find_callees","args":{"usr":"c:objc...(im)initWithProvider:","target_id":"Zoom"}}

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
    db = _parse_db(args)
    conn = _conn(db)
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
