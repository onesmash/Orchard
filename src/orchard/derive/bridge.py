"""cross_language_bridge_recovery phase.

Discovers ObjC ↔ Swift bridge candidates by matching symbols across
languages within the same target and writes BridgesTo edges.
"""

from __future__ import annotations

from orchard.normalize.identity import make_symbol_id


def run_bridge_recovery(conn, target_id: str, build_id: str) -> dict[str, int]:
    """Find cross-language bridge candidates and write BridgesTo edges.

    Strategies (in priority order):
      1. Name match: same base name + different language → confidence 0.70.
      2. USR correlation (deferred to M4).

    Uses MERGE for idempotency — repeated runs with the same data produce
    no new edges. Counts reflect only *new* edges written in this call.

    Parameters
    ----------
    conn
        Open Ladybug connection.
    target_id
        The build target identifier.
    build_id
        The build snapshot identifier.

    Returns
    -------
    dict
        Counters: ``bridges_by_name``, ``total``.
    """
    # Count existing edges for this provenance/build_id so we can report
    # only the *new* edges written in this call (delta-based idempotency).
    before = conn.execute(
        "MATCH ()-[r:BridgesTo {provenance: 'derive/bridge', build_id: $bid}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    before_count = int(before[0][0]) if before else 0

    # Strategy 1: Name + kind match across languages.
    # Simple cross-language scan using only Symbol nodes (no File/Target edges
    # required).  Symbols must differ in language, both be swift or objc, and
    # belong to the same target.
    rows = conn.execute(
        "MATCH (a:Symbol), (b:Symbol) "
        "WHERE a.name = b.name AND a.kind = b.kind "
        "  AND a.language <> b.language AND a.language IN ['swift','objc'] "
        "  AND b.language IN ['swift','objc'] "
        "  AND a.target_id = $tid AND b.target_id = $tid "
        "RETURN a.usr, b.usr LIMIT 5000",
        {"tid": target_id},
    ).get_all()

    # Deduplicate pairs: (a,b) and (b,a) are the same bridge.
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        usr_a, usr_b = row[0], row[1]
        pair_key = tuple(sorted([usr_a, usr_b]))
        pairs.add(pair_key)

    # Write bidirectional BridgesTo edges via MERGE.
    for usr_a, usr_b in pairs:
        for src_usr, tgt_usr in [(usr_a, usr_b), (usr_b, usr_a)]:
            conn.execute(
                "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
                "MERGE (a)-[:BridgesTo {bridge_kind: $kind, provenance: $prov, "
                "confidence: $conf, build_id: $bid}]->(b)",
                {
                    "src": make_symbol_id(target_id, src_usr),
                    "dst": make_symbol_id(target_id, tgt_usr),
                    "kind": "name_match",
                    "prov": "derive/bridge",
                    "conf": 0.70,
                    "bid": build_id,
                },
            )

    # Count after and compute delta — only *new* edges are reported.
    after = conn.execute(
        "MATCH ()-[r:BridgesTo {provenance: 'derive/bridge', build_id: $bid}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    after_count = int(after[0][0]) if after else 0

    new_edges = after_count - before_count
    return {
        "bridges_by_name": len(pairs) if new_edges > 0 else 0,
        "total": new_edges,
    }
