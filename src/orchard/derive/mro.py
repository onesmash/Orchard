"""MRO (Method Resolution Order) stage — compute method override chains.

Walks Inherits and Implements edges to find same-name methods, then writes
METHOD_OVERRIDES edges for correct dispatch resolution.

Inspired by GitNexus's MRO processor.
"""

from __future__ import annotations


def run_mro(conn, target_id: str) -> dict[str, int]:
    """Find and write method override relationships.

    Returns counts of overrides found and edges written.
    """
    # Find pairs: same method name, different USRs, one inherits from the other
    rows = conn.execute(
        "MATCH (child:Symbol)-[:Inherits]->(parent:Symbol) "
        "WHERE child.kind IN ['method','function'] "
        "  AND parent.kind IN ['class','struct'] "
        "  AND child.module = $tid "
        "MATCH (childMethod:Symbol) "
        "WHERE childMethod.name = child.name "
        "  AND childMethod.kind = 'method' "
        "RETURN DISTINCT child.usr, parent.usr, childMethod.name "
        "LIMIT 1000",
        {"tid": target_id},
    ).get_all()

    count = 0
    for row in rows:
        child_usr, parent_usr, name = row[0], row[1], row[2]
        # Find the parent's method with the same name
        p_rows = conn.execute(
            "MATCH (pm:Symbol)-[:Contains]->(parentMethod:Symbol) "
            "WHERE pm.usr = $pusr AND parentMethod.name = $name "
            "RETURN parentMethod.usr LIMIT 1",
            {"pusr": parent_usr, "name": name},
        ).get_all()
        if p_rows:
            # Write override edge
            conn.execute(
                "MATCH (a:Symbol {usr: $child}), (b:Symbol {usr: $parent}) "
                "MERGE (a)-[:Implements {source: 'derive/mro'}]->(b)",
                {"child": child_usr, "parent": p_rows[0][0]},
            )
            count += 1

    return {"overrides_found": count}
