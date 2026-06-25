"""Process detection — discover execution flows from entry points.

Finds functions with no internal callers (entry points), then performs BFS
forward through Calls edges to build Process nodes + STEP_IN_PROCESS edges.
"""

from __future__ import annotations

from collections import deque


def run_process_detection(conn, target_id: str) -> dict[str, int]:
    """Detect execution flows and write Process nodes + STEP_IN_PROCESS edges.

    Returns counts of processes found and steps written.
    """
    # Find entry points: symbols that CALL others but are not called internally
    entry_rows = conn.execute(
        "MATCH (s:Symbol)-[:Calls]->(:Symbol) "
        "WHERE s.module = $tid AND s.kind IN ['method','function'] "
        "AND NOT EXISTS { MATCH (:Symbol)-[:Calls]->(s) WHERE s.module = $tid } "
        "RETURN DISTINCT s.usr, s.name, s.kind "
        "LIMIT 100",
        {"tid": target_id},
    ).get_all()

    processes = 0
    steps_written = 0

    for entry in entry_rows:
        usr, name, kind = entry[0], entry[1], entry[2]
        process_id = f"process:{target_id}:{usr}"

        # Create Process node
        conn.execute(
            "MERGE (p:Process {id: $pid}) "
            "SET p.entry_name = $name, p.entry_kind = $kind",
            {"pid": process_id, "name": name, "kind": kind},
        )

        # BFS forward through Calls edges
        visited = {usr}
        queue = deque([(usr, 0)])  # (usr, step)
        max_steps = 50

        while queue and len(visited) < 200:
            current, step = queue.popleft()
            if step >= max_steps:
                continue

            callee_rows = conn.execute(
                "MATCH (s:Symbol {usr: $usr})-[:Calls]->(callee:Symbol) "
                "WHERE callee.module = $tid "
                "RETURN DISTINCT callee.usr LIMIT 20",
                {"usr": current, "tid": target_id},
            ).get_all()

            for cr in callee_rows:
                callee_usr = cr[0]
                # Write step edge
                conn.execute(
                    "MATCH (s:Symbol {usr: $s_usr}), (p:Process {id: $pid}) "
                    "MERGE (s)-[:STEP_IN_PROCESS {step: $step}]->(p)",
                    {"s_usr": callee_usr, "pid": process_id, "step": step},
                )
                steps_written += 1
                if callee_usr not in visited:
                    visited.add(callee_usr)
                    queue.append((callee_usr, step + 1))

        processes += 1

    return {"processes_found": processes, "steps_written": steps_written}
