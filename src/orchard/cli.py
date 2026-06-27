"""Orchard CLI — query the semantic graph from the command line.

Usage::

    orchard find_callers --usr s:myFunc [--db ~/.orchard/graph.db]
    orchard find_callees --usr s:myFunc
    orchard impact --usr s:myFunc
    orchard symbol  --usr s:myFunc
    orchard hierarchy --usr s:myFunc
    orchard ingest  --index-store <path> [--source-root <dir>]
    orchard setup   --mcp | --skill | --model   # one-shot configuration
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


def _parse_caller_callee_args(args: list[str]) -> tuple[str, str, str, bool, int, list[str]]:
    """Parse --usr, --target, --db, --include-noise, --depth, --relation-types."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--usr", required=True)
    ap.add_argument("--target", default="")
    ap.add_argument("--db", default="")
    ap.add_argument("--include-noise", action="store_true", default=False)
    ap.add_argument("--depth", type=int, default=1,
                    help="Multi-hop traversal depth (default: 1, direct only)")
    ap.add_argument("--relation-types", default="Calls",
                    help="Comma-separated edge types to traverse (default: Calls)")
    ns = ap.parse_args(args)
    rel_types = [t.strip() for t in ns.relation_types.split(",") if t.strip()]
    return ns.usr, ns.target, ns.db, ns.include_noise, ns.depth, rel_types


def cmd_find_callers(args: list[str]):
    usr, target, db, include_noise, depth, rel_types = _parse_caller_callee_args(args)
    from orchard.handlers.callers import CallerRequest, find_callers
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = find_callers(conn, CallerRequest(usr=usr, target_id=target, build_id=build_id,
                                          depth=depth, relation_types=rel_types))
    if not include_noise:
        from orchard.query.noise_filter import filter_noise
        filtered, removed = filter_noise(r.data)
        r.data = filtered
        r.noise_removed = removed
    _print_json(r.__dict__)
    conn.close()


def cmd_find_callees(args: list[str]):
    usr, target, db, include_noise, depth, rel_types = _parse_caller_callee_args(args)
    from orchard.handlers.callees import CalleeRequest, find_callees
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = find_callees(conn, CalleeRequest(usr=usr, target_id=target, build_id=build_id,
                                          depth=depth, relation_types=rel_types))
    if not include_noise:
        from orchard.query.noise_filter import filter_noise
        filtered, removed = filter_noise(r.data)
        r.data = filtered
        r.noise_removed = removed
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


def cmd_find_references(args: list[str]):
    usr, target, db = _parse_common(args)
    from orchard.handlers.references import ReferencesRequest, find_references
    conn = _conn(db)
    build_id = _default_build_id(conn, target)
    r = find_references(conn, ReferencesRequest(usr=usr, target_id=target, build_id=build_id))
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
                    help="Build target identifier(s), comma-separated for multiple. "
                         "Auto-detected from project name if omitted.")
    ap.add_argument("--db", default="",
                    help="Graph database path (default: <project>/.orchard/graph.db)")
    ap.add_argument("--incremental", action="store_true",
                    help="Only ingest files changed since last ingest")
    ap.add_argument("--symbolgraph", default="",
                    help="Path to a SymbolGraph JSON file to ingest alongside IndexStore data")
    ns = ap.parse_args(args)
    from orchard.ingest.indexstore import read_index_store, _unit_dir_mtime
    from orchard.normalize.identity import (
        upsert_symbols, upsert_calls, upsert_indexstore_rels,
        delete_symbols_for_files,
    )
    from orchard.ingest.symbolgraph import SymbolRecord
    from orchard.pipeline.runner import _map_indexstore_kind
    from orchard.ingest.state import load_state, save_state, touch_timestamp
    from pathlib import Path
    from orchard.build.xcode_settings import find_xcode_project, match_derived_data, get_derived_data_path

    index_store = ns.index_store
    source_root = str(Path(ns.source_root).resolve()) if ns.source_root else None

    # Parse comma-separated targets.
    targets: list[str] = [t.strip() for t in ns.target.split(",") if t.strip()] if ns.target else []

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
        if not targets:
            targets = [Path(project).stem]  # auto-detect from project name
        if not source_root:
            source_root = str(Path(ns.project_dir).resolve())
        print(f"auto-detected: --index-store {index_store}")
        print(f"auto-detected: --target {','.join(targets)}")
        if source_root:
            print(f"auto-detected: --source-root {source_root}")
        if len(candidates) > 1:
            print(f"note: {len(candidates)} matching DerivedData dirs found, using newest:")
            for dd, _, acc in candidates[:3]:
                print(f"  {dd}  (accessed {acc})")
    elif not ns.db:
        ns.db = str(Path(ns.project_dir) / ".orchard" / "graph.db")
    if not targets:
        print("error: --target is required when no Xcode project is auto-detected",
              file=sys.stderr)
        sys.exit(2)

    conn = _conn(ns.db)
    project_dir = str(Path(ns.project_dir).resolve())
    index_store_paths = {t: index_store for t in targets}

    # Resolve incremental mode.
    incremental_since: float | None = None
    old_state: dict | None = None
    if ns.incremental:
        old_state = load_state(project_dir)
        if old_state:
            incremental_since = old_state.get("last_ingest_ts")
            print(f"incremental: last ingest was {incremental_since}")
            prev_targets = old_state.get("targets", [])
            if prev_targets:
                print(f"incremental: previously ingested targets: "
                      f"{','.join(prev_targets)}")
        else:
            print("incremental: no previous state found, falling back to full ingest")

    # L1: IndexStore-level fast path — if no unit files changed since last
    # ingest, skip the entire scan (~100ms vs ~90s).  The unit directory mtime
    # is shared across all targets in the same IndexStore.
    if ns.incremental and incremental_since is not None:
        unit_ts = _unit_dir_mtime(index_store)
        if unit_ts <= incremental_since:
            print(f"incremental: no new units (unit mtime {unit_ts} <= "
                  f"{incremental_since})")
            conn.close()
            return

    t0 = time.monotonic()
    r, file_status = read_index_store(
        index_store, targets[0], source_root=source_root,
        incremental_since=incremental_since,
    )

    # Incremental cleanup: delete stale symbols for changed and deleted files
    # across ALL previously ingested targets.
    deleted_total = 0
    if file_status:
        changed = file_status.get("changed", [])
        all_files = file_status.get("all", [])
        old_files = set(old_state.get("files", []) if old_state else [])
        old_targets = old_state.get("targets", targets) if old_state else targets
        deleted_files = old_files - set(all_files)
        to_clean = changed + list(deleted_files)
        if to_clean:
            print(f"incremental: cleaning {len(changed)} changed + "
                  f"{len(deleted_files)} deleted files across "
                  f"{len(old_targets)} target(s)...")
            for tgt in old_targets:
                c = delete_symbols_for_files(conn, tgt, to_clean)
                deleted_total += c
            print(f"incremental: {deleted_total:,} old symbols deleted")
        if not r.symbols and not r.relations:
            # No changes — update state and exit.
            all_list = file_status.get("all", []) if file_status else []
            save_state(project_dir, touch_timestamp(), targets,
                       index_store_paths, files=all_list)
            print("incremental: no changes detected")
            conn.close()
            return

    print(f"ingest: {r.elapsed_s}s  {len(r.symbols):,} syms  "
          f"{len(r.relations):,} rels  {len(targets)} target(s)")

    # Upsert all symbols and relations for each target.
    # The IndexStore contains records for all built targets; each target gets
    # its own scoped copy in the graph keyed by ``{target}:{usr}``.
    for i, target in enumerate(targets):
        syms = [SymbolRecord(usr=s.usr, precise_id="", name=s.name,
                             kind=_map_indexstore_kind(s.symbol_kind),
                             module=s.module or target, language=s.language,
                             file_path=s.file_path or "", signature="",
                             access_level="public", container_usr=None)
                for s in r.symbols]
        upsert_symbols(conn, syms, target)
        upsert_calls(conn, r.relations, target, source="indexstore",
                     build_id="cli")
        upsert_indexstore_rels(conn, r.relations, target, source="indexstore",
                               build_id="cli")
        print(f"  [{i+1}/{len(targets)}] {target}: {len(syms):,} syms, "
              f"{len(r.relations):,} rels")

    # SymbolGraph ingest: parse JSON and upsert its symbols + relationships.
    if ns.symbolgraph:
        from orchard.ingest.symbolgraph import parse_symbolgraph
        from orchard.normalize.identity import upsert_symbol_rels
        t_sg = time.monotonic()
        sg = parse_symbolgraph(ns.symbolgraph, targets[0])
        if sg.symbols:
            upsert_symbols(conn, sg.symbols, targets[0])
        if sg.relationships:
            upsert_symbol_rels(conn, sg.relationships, targets[0])
        print(f"  symbolgraph: {len(sg.symbols):,} syms, "
              f"{len(sg.relationships):,} rels  ({time.monotonic()-t_sg:.1f}s)")

    # Community detection via Leiden algorithm.
    try:
        from orchard.derive.community_detection import run_community_detection
        for target in targets:
            t_cd = time.monotonic()
            result = run_community_detection(conn, target)
            print(f"  communities ({target}): {result['communities_found']} communities, "
                  f"{result['members_assigned']} members  ({time.monotonic()-t_cd:.1f}s)")
    except Exception:
        pass  # skip on mock/test databases

    # Process detection via entry-point scoring + BFS tracing.
    try:
        from orchard.derive.process_detection import detect_processes
        for target in targets:
            t_pd = time.monotonic()
            procs = detect_processes(conn, target)
            cross = sum(1 for p in procs if p.process_type == "cross_community")
            print(f"  processes ({target}): {len(procs)} detected "
                  f"({cross} cross-community)  ({time.monotonic()-t_pd:.1f}s)")
    except Exception:
        pass  # skip on mock/test databases

    print(f"done  {time.monotonic()-t0:.0f}s")

    # Persist state for next incremental run.
    # Full ingest: only save timestamp (no file list — we don't need it).
    # Incremental: save timestamp + file list from CLI output.
    if file_status and "all" in file_status:
        save_state(project_dir, touch_timestamp(), targets, index_store_paths,
                   files=file_status["all"])
    else:
        save_state(project_dir, touch_timestamp(), targets, index_store_paths)


def cmd_search(args: list[str]):
    """Search symbols by name pattern (regex, case-sensitive).

    Two modes:

    * **Name search** (default): ``--name <pattern>`` matches symbol names.
    * **Class search**: ``--class <ClassName>`` finds all methods of matching
      class/struct/enum/protocol symbols.  Combines with ``--target``,
      ``--kind`` (filters returned methods), and ``--limit``.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="orchard search")
    ap.add_argument("--name", default="", help="Regex pattern for symbol name (case-sensitive)")
    ap.add_argument("--class", "-c", dest="class_name", default="",
                    help="Search for a class/struct/enum/protocol by name and list its methods")
    ap.add_argument("--target", default="", help="Filter by target/module")
    ap.add_argument("--kind", default="", help="Filter by kind (class, method, function, etc.)")
    ap.add_argument("--language", default="", help="Filter by language (swift, objc, c, etc.)")
    ap.add_argument("--file", default="", help="Filter by file path (substring match)")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--db", default="")
    ns = ap.parse_args(args)

    if not ns.name and not ns.class_name:
        ap.error("either --name or --class is required")

    conn = _conn(ns.db)

    if ns.class_name:
        _cmd_search_class(conn, ns)
    else:
        _cmd_search_name(conn, ns)

    conn.close()


def _cmd_search_class(conn, ns):
    """Search for a class by name and list its methods."""
    from orchard.query.lookup import GraphLookup

    gl = GraphLookup(conn)
    class_pattern = _compile_search_pattern(ns.class_name)

    # Step 1: find matching class/struct/enum/protocol symbols.
    where = [
        "s.name =~ $pattern",
        "s.kind IN ['class', 'struct', 'enum', 'protocol']",
    ]
    params: dict = {"pattern": class_pattern, "limit": ns.limit}
    if ns.target:
        where.append("s.module = $target")
        params["target"] = ns.target
    if ns.file:
        where.append(_file_where(ns, params))

    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.module "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()

    owners = []
    for r in rows:
        owner = {"usr": r[0], "name": r[1], "kind": r[2], "module": r[3]}
        # Step 2: get methods for each matching class.
        methods = gl.methods_of(r[0], ns.target)
        # Step 3: apply --kind filter to returned methods (if given).
        if ns.kind:
            methods = [m for m in methods if m["kind"] == ns.kind]
        # Step 4: apply --limit to total methods across all owners.
        owners.append({"owner": owner, "methods": methods})

    # Trim total method count across all owners to --limit.
    total_methods = 0
    for entry in owners:
        if total_methods >= ns.limit:
            entry["methods"] = []
        elif total_methods + len(entry["methods"]) > ns.limit:
            entry["methods"] = entry["methods"][:ns.limit - total_methods]
        total_methods += len(entry["methods"])

    _print_json({
        "owners": owners,
        "total_methods": sum(len(e["methods"]) for e in owners),
    })


def _cmd_search_name(conn, ns):
    """Existing name-based symbol search."""
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
    if ns.file:
        where.append(_file_where(ns, params))
    rows = conn.execute(
        f"MATCH (s:Symbol) WHERE {' AND '.join(where)} "
        "RETURN s.usr, s.name, s.kind, s.language, s.module "
        "ORDER BY s.name LIMIT $limit",
        params,
    ).get_all()
    results = [{"usr": r[0], "name": r[1], "kind": r[2], "language": r[3], "module": r[4]} for r in rows]
    _print_json({"count": len(results), "results": results})


def cmd_pipe(args: list[str]):
    """Execute multiple queries from stdin (JSONL) in a single process.

    Reads one JSON object per line from stdin.  Each object must have ``"cmd"``
    (one of: search, find_callers, find_callees, impact, symbol, hierarchy)
    and ``"args"`` (a dict of keyword arguments).

    Example stdin::

        {"cmd":"search","args":{"name":"initWithProvider","target":"YourModule"}}
        {"cmd":"find_callers","args":{"usr":"c:objc...(im)initWithProvider:"}}
        {"cmd":"find_callees","args":{"usr":"c:objc...(im)initWithProvider:"}}

    For find_callers / find_callees, add ``"include_noise": true`` to keep
    C++ operator/logging noise in the output (filtered by default).

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
    req_keys = {"usr"}

    if cmd == "search":
        return _pipe_search(conn, args)

    if cmd == "find_callers":
        from orchard.handlers.callers import CallerRequest, find_callers
        r = find_callers(conn, CallerRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", ""),
            depth=args.get("depth", 1),
            relation_types=args.get("relation_types", ["Calls"]),
        ))
        if not args.get("include_noise", False):
            from orchard.query.noise_filter import filter_noise
            filtered, removed = filter_noise(r.data)
            r.data = filtered
            r.noise_removed = removed
        return r.__dict__

    if cmd == "find_references":
        from orchard.handlers.references import ReferencesRequest, find_references
        r = find_references(conn, ReferencesRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", ""),
        ))
        return r.__dict__

    if cmd == "find_callees":
        from orchard.handlers.callees import CalleeRequest, find_callees
        r = find_callees(conn, CalleeRequest(
            usr=args.get("usr", ""), target_id=args.get("target_id", ""),
            depth=args.get("depth", 1),
            relation_types=args.get("relation_types", ["Calls"]),
        ))
        if not args.get("include_noise", False):
            from orchard.query.noise_filter import filter_noise
            filtered, removed = filter_noise(r.data)
            r.data = filtered
            r.noise_removed = removed
        return r.__dict__

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

    if cmd == "audit":
        return _execute_pipe_audit(conn, args)

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


def _file_where(ns, params: dict) -> str:
    """Add a file-path filter clause and its parameter to *params*."""
    params["file_pattern"] = f".*{ns.file}.*"
    return "s.file_path =~ $file_pattern"


def _pipe_search(conn, args: dict):
    """Direct search query — no handler overhead for this path.

    Two modes:
    * ``class`` provided → class-search: find matching classes and list their methods.
    * ``name`` provided  → name-search: match symbol names by regex.
    """
    class_name = args.get("class", "")
    if class_name:
        return _pipe_search_class(conn, args)
    return _pipe_search_name(conn, args)


def _pipe_search_class(conn, args: dict):
    """Pipe-mode class search: find class by name and list its methods."""
    from orchard.query.lookup import GraphLookup

    gl = GraphLookup(conn)
    class_pattern = _compile_search_pattern(args["class"])
    target = args.get("target", "")
    kind_filter = args.get("kind", "")
    limit = args.get("limit", 20)

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

    return {
        "owners": owners,
        "total_methods": sum(len(e["methods"]) for e in owners),
    }


def _pipe_search_name(conn, args: dict):
    """Pipe-mode name search: match symbol names by regex."""
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
    if args.get("file"):
        params["file_pattern"] = f".*{args['file']}.*"
        where.append("s.file_path =~ $file_pattern")
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
# Audit command
# ---------------------------------------------------------------------------

ANOMALY_THRESHOLD = 100
"""Modules with fewer than this many symbols are flagged as potential gaps."""


def _discover_xcode_targets(project_dir: str) -> list[str]:
    """Discover Xcode workspace/project targets via ``xcodebuild -list``.

    Returns a list of target names, or an empty list on failure.
    """
    import subprocess
    from pathlib import Path

    root = Path(project_dir).resolve()
    # Prefer workspace over project.
    workspace = None
    project = None
    for entry in root.iterdir():
        if entry.suffix == ".xcworkspace" and not entry.name.startswith("."):
            workspace = str(entry)
            break
        if entry.suffix == ".xcodeproj" and not entry.name.startswith("."):
            project = str(entry)

    list_args: list[str] = []
    if workspace:
        list_args = ["xcodebuild", "-list", "-workspace", workspace]
    elif project:
        list_args = ["xcodebuild", "-list", "-project", project]
    else:
        return []

    try:
        result = subprocess.run(
            list_args,
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    if result.returncode != 0:
        return []

    # Parse targets from xcodebuild -list output:
    #     Targets:
    #         TargetA
    #         TargetB
    targets: list[str] = []
    in_targets = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "Targets:":
            in_targets = True
            continue
        if in_targets:
            if stripped == "" or stripped.startswith("Build Configurations:") or stripped.startswith("Schemes:"):
                break
            targets.append(stripped)

    return targets


def _format_audit_table(stats: list[dict], xcode_targets: list[str] | None = None) -> str:
    """Format per-module symbol counts as a text table.

    Pivots per-kind counts into columns.  Returns a multi-line string.
    """
    from collections import defaultdict

    # Pivot: {module: {kind: count}}.  Treat None module as "(unknown)".
    modules: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_kinds: set[str] = set()
    for row in stats:
        mod = row["module"] or "(unknown)"
        kind = row["kind"]
        cnt = row["count"]
        modules[mod][kind] += cnt
        all_kinds.add(kind)

    # Total per module.
    total_by_module = {mod: sum(d.values()) for mod, d in modules.items()}

    # Sort modules by total symbols descending.
    sorted_modules = sorted(modules, key=lambda m: total_by_module[m], reverse=True)

    # Build column order: Symbols, then non-standard alphabetically.
    priority_kinds = {"class", "method", "protocol", "struct", "enum", "extension",
                      "function", "property", "variable", "typealias"}
    kind_cols = [k for k in priority_kinds if k in all_kinds]
    kind_cols += sorted(all_kinds - set(priority_kinds))
    col_headers = ["Module", "Symbols"] + [k.capitalize() for k in kind_cols]
    all_cols = ["module", "total"] + kind_cols

    # Determine anomaly flags.
    anomaly_mods: set[str] = set()
    if xcode_targets:
        xcode_set = set(xcode_targets)
        for mod in sorted_modules:
            if mod in xcode_set and total_by_module[mod] < ANOMALY_THRESHOLD:
                anomaly_mods.add(mod)

    # Collect rows.
    rows: list[list[str]] = []
    for mod in sorted_modules:
        flag = " ⚠ UNEXPECTED GAP" if mod in anomaly_mods else ""
        row = [mod + flag, str(total_by_module[mod])]
        for k in kind_cols:
            row.append(str(modules[mod].get(k, 0)))
        rows.append(row)

    # Compute column widths.
    widths = [len(h) for h in col_headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    # Build output.
    def fmt_row(cells: list[str]) -> str:
        return " | ".join(cell.ljust(w) for cell, w in zip(cells, widths))

    lines = [fmt_row(col_headers), "-|-".join("-" * w for w in widths)]
    for row in rows:
        lines.append(fmt_row(row))

    if anomaly_mods:
        lines.append("")
        lines.append(f"⚠  {len(anomaly_mods)} module(s) below threshold ({ANOMALY_THRESHOLD} symbols):")
        for m in sorted(anomaly_mods):
            lines.append(f"   {m} ({total_by_module[m]:,} symbols)")

    return "\n".join(lines)


def cmd_process_list(args: list[str]):
    # Subcommand dispatch: "orchard process show <id>"
    if args and args[0] == "show":
        cmd_process_show(args[1:])
        return
    db = _parse_db(args)
    conn = _conn(db)
    rows = conn.execute(
        "MATCH (p:Process) RETURN p.id, p.entry_name, p.entry_kind, "
        "p.label, p.process_type, p.step_count ORDER BY p.id"
    ).get_all()
    procs = [{"id": r[0], "entry_name": r[1], "entry_kind": r[2],
              "label": r[3], "process_type": r[4], "step_count": r[5]}
             for r in rows]
    _print_json({"count": len(procs), "processes": procs})
    conn.close()


def cmd_process_show(args: list[str]):
    """Show a single process with its full step chain."""
    pid = args[0] if args else ""
    db = _parse_db(args[1:])
    conn = _conn(db)
    rows = conn.execute(
        "MATCH (s:Symbol)-[r:STEP_IN_PROCESS]->(p:Process {id: $id}) "
        "RETURN s.name, s.usr, s.kind, r.step ORDER BY r.step",
        {"id": pid},
    ).get_all()
    steps = [{"step": r[3], "name": r[0], "usr": r[1], "kind": r[2]} for r in rows]
    proc_row = conn.execute(
        "MATCH (p:Process {id: $id}) RETURN p.entry_name, p.label, p.process_type, p.step_count",
        {"id": pid},
    ).get_all()
    proc_info = {}
    if proc_row:
        proc_info = {"entry_name": proc_row[0][0], "label": proc_row[0][1],
                     "process_type": proc_row[0][2], "step_count": proc_row[0][3]}
    _print_json({"process": proc_info, "steps": steps, "step_count": len(steps)})
    conn.close()


def cmd_audit(args: list[str]):
    """Audit the graph database: module coverage, symbol counts by kind, gap detection.

    When ``--project-dir`` is given, the command compares graph modules against
    Xcode workspace targets and flags any framework target with fewer than
    ``ANOMALY_THRESHOLD`` (100) symbols as a potential gap.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="orchard audit")
    ap.add_argument("--project-dir", default="",
                    help="Xcode project directory for target discovery and gap detection")
    ap.add_argument("--format", choices=["table", "json"], default="table",
                    help="Output format (default: table)")
    ap.add_argument("--db", default="",
                    help="Graph database path")
    ns = ap.parse_args(args)

    conn = _conn(ns.db)
    from orchard.query.lookup import GraphLookup
    gl = GraphLookup(conn)
    stats = gl.module_stats()

    # Discover Xcode targets if project-dir is given.
    xcode_targets: list[str] | None = None
    if ns.project_dir:
        xcode_targets = _discover_xcode_targets(ns.project_dir)
        if xcode_targets and ns.format == "table":
            print(f"Xcode targets discovered: {len(xcode_targets)}")
            print()

    if ns.format == "json":
        result = {
            "modules": stats,
            "xcode_targets": xcode_targets,
            "anomaly_threshold": ANOMALY_THRESHOLD,
        }
        if xcode_targets:
            result["gaps"] = _detect_gaps(stats, xcode_targets)
        _print_json(result)
    else:
        table = _format_audit_table(stats, xcode_targets)
        print(table)
        # Print summary line.
        total_symbols = sum(r["count"] for r in stats)
        unique_modules = len({r["module"] for r in stats})
        unique_kinds = len({r["kind"] for r in stats})
        print()
        print(f"Total: {total_symbols:,} symbols across {unique_modules} modules ({unique_kinds} kinds)")

    conn.close()


def _detect_gaps(stats: list[dict], xcode_targets: list[str]) -> list[dict]:
    """Detect modules with suspiciously low symbol counts relative to Xcode targets."""
    from collections import defaultdict
    total_by_module: dict[str, int] = defaultdict(int)
    for row in stats:
        total_by_module[row["module"]] += row["count"]

    xcode_set = set(xcode_targets)
    gaps = []
    for target in sorted(xcode_targets):
        count = total_by_module.get(target, 0)
        if count < ANOMALY_THRESHOLD:
            gaps.append({
                "target": target,
                "symbols": count,
                "status": "UNEXPECTED GAP" if count > 0 else "MISSING",
                "threshold": ANOMALY_THRESHOLD,
            })
    return gaps


def _execute_pipe_audit(conn, args: dict) -> dict:
    """Handle ``{"cmd": "audit", ...}`` in pipe mode."""
    from orchard.query.lookup import GraphLookup
    gl = GraphLookup(conn)
    stats = gl.module_stats()

    project_dir = args.get("project_dir", "")
    xcode_targets = None
    if project_dir:
        xcode_targets = _discover_xcode_targets(project_dir)

    result: dict = {
        "modules": stats,
        "xcode_targets": xcode_targets,
        "anomaly_threshold": ANOMALY_THRESHOLD,
    }
    if xcode_targets:
        result["gaps"] = _detect_gaps(stats, xcode_targets)
    return result


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


from orchard.setup import cmd_setup

COMMANDS: dict[str, tuple] = {
    "search":        (cmd_search,        "Find symbols by name (substring or regex)"),
    "find_callers":  (cmd_find_callers,  "List all callers of a symbol"),
    "find_callees":  (cmd_find_callees,  "List all symbols called by a symbol"),
    "impact":        (cmd_impact,        "Blast-radius analysis with risk scoring"),
    "symbol":        (cmd_symbol,        "Show metadata for a single symbol"),
    "find_references": (cmd_find_references, "Find incoming and outgoing references for a symbol"),
    "hierarchy":     (cmd_hierarchy,     "Show type hierarchy (supertypes/subtypes)"),
    "ingest":        (cmd_ingest,        "Build the graph from Xcode IndexStore data"),
    "stats":         (cmd_stats,         "Database overview and freshness check"),
    "audit":         (cmd_audit,         "Module coverage report with Xcode target gap detection"),
    "process":       (cmd_process_list,  "List detected execution flows (Process nodes)"),
    "pipe":          (cmd_pipe,          "Batch queries via stdin JSONL (3+ queries)"),
    "setup":         (cmd_setup,         "Install MCP config + skill + download model"),
}


def _cmd_list() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = []
    for name, (_, desc) in COMMANDS.items():
        lines.append(f"  {name:<{width}}  {desc}")
    return "\n".join(lines)


_HELP = f"""\
Usage: orchard <command> [args]

  An Apple semantic graph CLI built on Xcode IndexStore data.
  Queries a per-project .orchard/graph.db (KuzuDB) with zero config.

Commands:
{_cmd_list()}

Use 'orchard <command> --help' for detailed options on each command.
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(_HELP)
        return
    cmd = sys.argv[1]
    entry = COMMANDS.get(cmd)
    if entry is None:
        names = ", ".join(COMMANDS)
        print(f"unknown command: {cmd}\ncommands: {names}", file=sys.stderr)
        sys.exit(2)
    entry[0](sys.argv[2:])


if __name__ == "__main__":
    main()
