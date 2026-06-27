"""Community detection via label propagation for functional domain discovery.

Groups symbols into communities based on co-occurrence in call graphs and
structural relationships.  Uses label propagation (Python-side) with CSV
batch writes for Community nodes and MEMBER_OF edges.
"""

from __future__ import annotations

from collections import defaultdict
import csv, os, tempfile


def run_community_detection(conn, target_id: str) -> dict[str, int]:
    """Detect communities and write Community nodes + MEMBER_OF edges."""

    # Load functional-proximity edges only (no Contains — structural, not functional).
    # GitNexus uses Calls + Extends + Implements; orchard uses Calls + Inherits + ConformsTo.
    adj: dict[str, set[str]] = defaultdict(set)
    for rel_type in ("Calls", "Inherits", "ConformsTo"):
        rows = conn.execute(
            f"MATCH (a:Symbol)-[:{rel_type}]->(b:Symbol) "
            f"RETURN a.usr, b.usr"
        ).get_all()
        for row in rows:
            adj[row[0]].add(row[1])
            adj[row[1]].add(row[0])

    if not adj:
        return {"communities_found": 0, "members_assigned": 0}

    # Skip singletons: degree-1 nodes cost iteration time but become singletons
    # or get absorbed into their single neighbor's community (GitNexus pattern).
    if len(adj) > 10000:
        adj = {k: v for k, v in adj.items() if len(v) >= 2}

    # Label propagation.
    labels: dict[str, int] = {}
    for i, node in enumerate(adj):
        labels[node] = i

    for _ in range(20):
        changed = False
        for node in adj:
            if not adj[node]:
                continue
            counts: dict[int, int] = defaultdict(int)
            for nb in adj[node]:
                counts[labels.get(nb, 0)] += 1
            if counts:
                best = max(counts, key=counts.get)
                if labels[node] != best:
                    labels[node] = best
                    changed = True
        if not changed:
            break

    # Group by label.
    groups: dict[int, set[str]] = defaultdict(set)
    for node, lbl in labels.items():
        groups[lbl].add(node)

    # CSV batch write Community nodes.
    csv_dir = tempfile.mkdtemp()
    comm_path = os.path.join(csv_dir, "communities.csv")
    with open(comm_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for lbl, members in groups.items():
            if len(members) < 3:
                continue
            w.writerow([f"community:{target_id}:{lbl}", len(members)])
    try:
        conn.execute(f"COPY Community FROM '{comm_path}' (HEADER=false)")
    except Exception:
        pass  # Community table may not exist yet
    os.unlink(comm_path)

    # CSV batch write MEMBER_OF edges.
    rel_path = os.path.join(csv_dir, "member_of.csv")
    with open(rel_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for lbl, members in groups.items():
            if len(members) < 3:
                continue
            cid = f"community:{target_id}:{lbl}"
            for usr in members:
                w.writerow([usr, cid])
    try:
        conn.execute(f"COPY MEMBER_OF FROM '{rel_path}' (HEADER=false)")
    except Exception:
        pass
    os.unlink(rel_path)
    os.rmdir(csv_dir)

    return {"communities_found": len(groups), "members_assigned": len(labels)}
