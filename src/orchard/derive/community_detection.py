"""Community detection via Leiden algorithm for functional domain discovery."""

from __future__ import annotations

from collections import defaultdict
import csv, os, tempfile

import igraph as ig
import leidenalg


def run_community_detection(conn, scope_id: str) -> dict[str, int]:
    """Detect communities via Leiden and write Community + MEMBER_OF edges."""
    adj: dict[str, set[str]] = defaultdict(set)
    for rel_type in ("Calls", "Inherits", "ConformsTo"):
        if rel_type == "Calls":
            rows = conn.execute(
                "MATCH (a:Symbol)-[r:Calls]->(b:Symbol) "
                "WHERE r.confidence IS NULL OR r.confidence >= 0.5 "
                "RETURN a.usr, b.usr"
            ).get_all()
        else:
            rows = conn.execute(
                f"MATCH (a:Symbol)-[:{rel_type}]->(b:Symbol) "
                f"RETURN a.usr, b.usr"
            ).get_all()
        for row in rows:
            adj[row[0]].add(row[1])
            adj[row[1]].add(row[0])

    if not adj:
        return {"communities_found": 0, "members_assigned": 0}

    # Skip degree-1 singletons (GitNexus pattern for large graphs).
    if len(adj) > 10000:
        adj = {k: v for k, v in adj.items() if len(v) >= 2}

    # Build igraph from adjacency.
    usr_list = list(adj.keys())
    usr_to_idx = {u: i for i, u in enumerate(usr_list)}
    g = ig.Graph(n=len(usr_list))
    edges = []
    for src, targets in adj.items():
        si = usr_to_idx[src]
        for tgt in targets:
            ti = usr_to_idx.get(tgt)
            if ti is not None and si != ti:
                edges.append((si, ti))
    g.add_edges(edges)

    # Leiden partition with deterministic seed.
    partition = leidenalg.find_partition(
        g, leidenalg.ModularityVertexPartition,
        n_iterations=2, seed=0xc0de,
    )

    # Group by community.
    groups: dict[int, set[str]] = defaultdict(set)
    for i, comm_id in enumerate(partition.membership):
        groups[comm_id].add(usr_list[i])

    # CSV batch write Community nodes + MEMBER_OF edges (unchanged).
    csv_dir = tempfile.mkdtemp()
    comm_path = os.path.join(csv_dir, "communities.csv")
    with open(comm_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for lbl, members in groups.items():
            if len(members) < 2:
                continue
            w.writerow([f"community:{scope_id}:{lbl}", len(members)])
    try:
        conn.execute(f"COPY Community FROM '{comm_path}' (HEADER=false)")
    except Exception:
        pass
    os.unlink(comm_path)

    rel_path = os.path.join(csv_dir, "member_of.csv")
    with open(rel_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        for lbl, members in groups.items():
            if len(members) < 2:
                continue
            cid = f"community:{scope_id}:{lbl}"
            for usr in members:
                w.writerow([usr, cid])
    try:
        conn.execute(f"COPY MEMBER_OF FROM '{rel_path}' (HEADER=false)")
    except Exception:
        pass
    os.unlink(rel_path)
    os.rmdir(csv_dir)

    return {"communities_found": len(groups), "members_assigned": sum(len(m) for m in groups.values())}
