"""Process detection via entry-point scoring + BFS forward tracing."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict

from orchard.query.lookup import GraphLookup


@dataclass
class ProcessNode:
    id: str
    label: str
    heuristic_label: str
    process_type: str
    step_count: int
    communities: list[str] = field(default_factory=list)
    entry_name: str = ""
    entry_kind: str = ""


def _entry_point_score(conn, max_candidates: int = 200) -> list[dict]:
    """Score symbols as potential process entry points.

    Uses weighted callee/caller ratio with naming-pattern boosts and a
    blacklist for noise symbols (accessors, dealloc, UI delegate stubs).
    """
    rows = conn.execute(
        "MATCH (s:Symbol)-[:Calls]->(c:Symbol) "
        "WITH s, count(c) AS callee_count "
        "OPTIONAL MATCH (caller:Symbol)-[:Calls]->(s) "
        "WITH s, callee_count, count(caller) AS caller_count "
        "WHERE callee_count >= 3 "
        "RETURN s.usr, s.name, s.kind, s.module, "
        "callee_count, caller_count "
        "ORDER BY callee_count DESC LIMIT $n",
        {"n": max_candidates},
    ).get_all()

    import re

    # High-value entry patterns (weight multiplier).
    _ENTRY_BOOST = [
        (re.compile(r"^(application|scene|userNotificationCenter):"), 3.0),
        (re.compile(r"^(handle|Handle|didReceive|onReceive)"), 2.5),
        (re.compile(r"^(imCmd|confNoti|notify|onConf|call)"), 2.0),
        (re.compile(r"^(viewDid|onLogin|onStart|pushNoti|report)"), 1.5),
    ]

    # Utility/penalty patterns: reduce score by ×0.5 (GitNexus soft penalty).
    # These are NOT excluded — a symbol with high call ratio still ranks high.
    _ENTRY_PENALTY = [
        re.compile(r"^(getter:|setter:|dealloc|\.cxx_destruct|init$|initWith)"),
        re.compile(r"^(tableView:|collectionView:|numberOfSections|numberOfRows)"),
        re.compile(r"^(itemsWith|actionsWith|onRender|onMoreMenu|loadSubviews?$)"),
        re.compile(r"^(refreshUI|find_by_|dispatchJSEvent|getCallOut)"),
    ]

    def _score(name: str, callees: int, callers: int) -> float:
        ratio = callees / (callers + 1)
        # Soft penalty (GitNexus pattern): utility-like names get ×0.5
        for pat in _ENTRY_PENALTY:
            if pat.search(name or ""):
                ratio *= 0.5
                break
        boost = 1.0
        for pat, weight in _ENTRY_BOOST:
            if pat.search(name or ""):
                boost = max(boost, weight)
        return ratio * boost

    scored = []
    for r in rows:
        usr, name, kind, module, callees, callers = r
        s = _score(name or "", callees, callers)
        scored.append({
                "usr": usr, "name": name, "kind": kind, "module": module,
                "callee_count": callees, "caller_count": callers, "score": s,
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def _build_calls_adjacency(conn) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Load all Calls edges into an in-memory adjacency list + USR→metadata map.

    Follows GitNexus pattern: one Cypher query, zero DB calls during BFS.
    """
    from collections import deque
    rows = conn.execute(
        "MATCH (a:Symbol)-[r:Calls]->(b:Symbol) "
        "WHERE r.confidence IS NULL OR r.confidence >= 0.5 "
        "RETURN a.usr, b.usr, b.name, b.module, b.kind, b.language"
    ).get_all()
    adj: dict[str, list[str]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for r in rows:
        adj[r[0]].append(r[1])
        if r[1] not in meta:
            meta[r[1]] = {"usr": r[1], "name": r[2], "module": r[3],
                          "kind": r[4], "language": r[5],
                          "reason": "source_direct"}
    return adj, meta


def _bfs_depth(adj: dict[str, list[str]], meta: dict[str, dict],
               entry_usr: str, max_depth: int) -> list[dict]:
    """In-memory BFS from *entry_usr* up to *max_depth* hops."""
    seen: set[str] = {entry_usr}
    results: list[dict] = []
    frontier: list[str] = [entry_usr]
    for d in range(1, max_depth + 1):
        next_frontier: list[str] = []
        for usr in frontier:
            for cu in adj.get(usr, []):
                if cu not in seen:
                    seen.add(cu)
                    callee = meta.get(cu, {"usr": cu, "name": cu, "module": "",
                                           "kind": "", "language": "",
                                           "reason": "source_direct"})
                    results.append({**callee, "depth": d})
                    next_frontier.append(cu)
        if not next_frontier:
            break
        frontier = next_frontier
    return results


def detect_processes(
    conn,
    scope_id: str = "",
    max_processes: int = 75,
    max_depth: int = 10,
    min_steps: int = 3,
) -> list[ProcessNode]:
    """Detect execution flows and write Process nodes + STEP_IN_PROCESS edges."""
    entries = _entry_point_score(conn)
    if not entries:
        return []

    # One-shot load of all Calls edges → in-memory BFS (GitNexus pattern).
    adj, meta = _build_calls_adjacency(conn)

    community_rows = conn.execute(
        "MATCH (s:Symbol)-[:MEMBER_OF]->(c:Community) RETURN s.usr, c.id"
    ).get_all()
    usr_to_communities: dict[str, set[str]] = defaultdict(set)
    for r in community_rows:
        usr_to_communities[r[0]].add(r[1])

    processes: list[ProcessNode] = []
    seen: set[str] = set()
    proc_callees: dict[str, list[dict]] = {}

    for entry in entries:
        if len(processes) >= max_processes:
            break
        if entry["usr"] in seen:
            continue
        seen.add(entry["usr"])

        callees = _bfs_depth(adj, meta, entry["usr"], max_depth)
        if len(callees) < min_steps:
            continue

        terminal = callees[-1]
        communities_used: set[str] = set()
        for c in callees:
            communities_used.update(usr_to_communities.get(c["usr"], set()))
        communities_used.update(usr_to_communities.get(entry["usr"], set()))

        proc_id = f"proc_{scope_id}_{len(processes)}"
        proc = ProcessNode(
            id=proc_id,
            label=f"{entry['name']} → {terminal['name']}",
            heuristic_label=f"{entry['name']} → {terminal['name']}",
            process_type="cross_community" if len(communities_used) > 1 else "intra_community",
            step_count=len(callees) + 1,
            communities=sorted(communities_used),
            entry_name=entry["name"],
            entry_kind=entry["kind"],
        )
        processes.append(proc)
        proc_callees[proc_id] = callees

    # CSV batch write Process nodes + STEP_IN_PROCESS edges.
    import csv, os, tempfile
    csv_dir = tempfile.mkdtemp()

    proc_path = os.path.join(csv_dir, "processes.csv")
    with open(proc_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for proc in processes:
            w.writerow([proc.id, proc.entry_name, proc.entry_kind,
                        proc.label, proc.process_type, proc.step_count])
    conn.execute(f"COPY Process FROM '{proc_path}' (HEADER=false)")

    step_path = os.path.join(csv_dir, "steps.csv")
    with open(step_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for proc in processes:
            for step, callee in enumerate(proc_callees[proc.id], start=1):
                w.writerow([callee["usr"], proc.id, step])
    conn.execute(f"COPY STEP_IN_PROCESS FROM '{step_path}' (HEADER=false)")

    os.unlink(proc_path)
    os.unlink(step_path)
    os.rmdir(csv_dir)

    return processes
