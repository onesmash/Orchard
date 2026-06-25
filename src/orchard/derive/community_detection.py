"""Community detection via label propagation for functional domain discovery.

Groups symbols into communities based on co-occurrence in call graphs and
structural relationships.  Uses a simple label propagation algorithm as
default; Leiden (igraph) is an optional upgrade.
"""

from __future__ import annotations

from collections import defaultdict


def run_community_detection(conn, target_id: str) -> dict[str, int]:
    """Detect communities and write Community nodes + MEMBER_OF edges.

    Strategy: label propagation on the co-occurrence graph built from
    Calls + Contains + Inherits edges.
    """
    # Build adjacency: symbol → neighboring symbols
    adj: dict[str, set[str]] = defaultdict(set)

    for rel_type in ("Calls", "Contains", "Inherits", "ConformsTo"):
        rows = conn.execute(
            f"MATCH (a:Symbol)-[:{rel_type}]->(b:Symbol) "
            f"WHERE a.module = $tid AND b.module = $tid "
            f"RETURN a.usr, b.usr LIMIT 10000",
            {"tid": target_id},
        ).get_all()
        for row in rows:
            adj[row[0]].add(row[1])
            adj[row[1]].add(row[0])

    if not adj:
        return {"communities_found": 0, "members_assigned": 0}

    # Label propagation
    labels: dict[str, int] = {}
    for i, node in enumerate(adj):
        labels[node] = i

    changed = True
    for _ in range(20):
        if not changed:
            break
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

    # Group by label
    groups: dict[int, set[str]] = defaultdict(set)
    for node, lbl in labels.items():
        groups[lbl].add(node)

    # Write Community nodes + MEMBER_OF edges
    for lbl, members in groups.items():
        if len(members) < 3:
            continue
        # Find most common module or name as label
        community_id = f"community:{target_id}:{lbl}"
        conn.execute(
            "MERGE (c:Community {id: $cid}) SET c.size = $size",
            {"cid": community_id, "size": len(members)},
        )
        for member_usr in members:
            conn.execute(
                "MATCH (s:Symbol {usr: $usr}), (c:Community {id: $cid}) "
                "MERGE (s)-[:MEMBER_OF]->(c)",
                {"usr": member_usr, "cid": community_id},
            )

    return {"communities_found": len(groups), "members_assigned": len(labels)}
