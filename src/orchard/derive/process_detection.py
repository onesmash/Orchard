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
    """Score symbols as potential process entry points by callee/caller ratio."""
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

    entry_patterns = (
        "handle", "Handle", "didReceive", "application:", "scene:",
        "viewDidLoad", "onLogin", "onStart", "main", "entry",
    )
    scored = []
    for r in rows:
        usr, name, kind, module, callees, callers = r
        name_boost = 1.5 if any(p in (name or "") for p in entry_patterns) else 1.0
        score = (callees / (callers + 1)) * name_boost
        scored.append({
            "usr": usr, "name": name, "kind": kind, "module": module,
            "callee_count": callees, "caller_count": callers, "score": score,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def detect_processes(
    conn,
    target_id: str = "",
    max_processes: int = 75,
    max_depth: int = 10,
    min_steps: int = 3,
) -> list[ProcessNode]:
    """Detect execution flows and write Process nodes + STEP_IN_PROCESS edges."""
    g = GraphLookup(conn)

    entries = _entry_point_score(conn)
    if not entries:
        return []

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

        callees = g.callees_of_depth(entry["usr"], target_id, depth=max_depth,
                                     relation_types=["Calls"])
        if len(callees) < min_steps:
            continue

        terminal = callees[-1]
        communities_used: set[str] = set()
        for c in callees:
            communities_used.update(usr_to_communities.get(c["usr"], set()))
        communities_used.update(usr_to_communities.get(entry["usr"], set()))

        proc_id = f"proc_{target_id}_{len(processes)}"
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

    # Write Process nodes + STEP_IN_PROCESS edges in one pass.
    for proc in processes:
        conn.execute(
            "CREATE (:Process {id: $id, entry_name: $name, entry_kind: $kind})",
            {"id": proc.id, "name": proc.entry_name, "kind": proc.entry_kind},
        )
        for step, callee in enumerate(proc_callees[proc.id], start=1):
            conn.execute(
                "MATCH (s:Symbol {usr: $usr}), (p:Process {id: $pid}) "
                "CREATE (s)-[:STEP_IN_PROCESS {step: $step}]->(p)",
                {"usr": callee["usr"], "pid": proc.id, "step": step},
            )

    return processes
