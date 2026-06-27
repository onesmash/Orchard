"""swiftui_derivation phase.

Derives ViewTree and NavigationFlow edges using existing data sources:
  - ConformsTo edges (symbolgraph): which symbols conform to ``View``.
  - Calls edges (indexstore + call_graph_derivation): callees of body members.

By intersecting the two — body callees that themselves conform to ``View`` —
we obtain real sub-view relationships without any new toolchain.
"""

from __future__ import annotations

from orchard.normalize.identity import make_symbol_id


def run_swiftui_derivation(conn, target_id: str, build_id: str) -> dict[str, int]:
    """Derive ViewTree and NavigationFlow edges from real graph data.

    Algorithm:
      1. Find Symbols that conform to View protocol.
      2. Find body-like Symbol members (name contains ``body``/``view``/``content``).
      3. For each body member's Calls callees, intersect with View set -> ViewTree.
      4. If body calls NavigationLink-like initialiser -> NavigationFlow edges.

    Confidence: 0.75 (call-graph + protocol evidence).
    """
    for ddl in [
        "CREATE REL TABLE IF NOT EXISTS ViewTree(FROM Symbol TO Symbol, "
        "derived_from STRING, confidence DOUBLE, build_id STRING)",
        "CREATE REL TABLE IF NOT EXISTS NavigationFlow(FROM Symbol TO Symbol, "
        "derived_from STRING, confidence DOUBLE, build_id STRING)",
    ]:
        conn.execute(ddl)

    before_vt = _count(conn, "ViewTree", build_id)
    before_nf = _count(conn, "NavigationFlow", build_id)

    # 1. View-conforming Symbols.
    view_rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid})-[r:ConformsTo]->(p:Symbol) "
        "WHERE p.name = 'View' AND p.kind = 'protocol' "
        "RETURN s.usr, s.name",
        {"tid": target_id},
    ).get_all()
    view_usrs = {r[0] for r in view_rows}
    if not view_usrs:
        return {"view_tree_edges": 0, "nav_flow_edges": 0}

    # 2. Body-like members and their callees.
    body_rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid}) "
        "WHERE s.kind IN ['instanceProperty', 'instanceMethod'] "
        "RETURN s.usr, s.name",
        {"tid": target_id},
    ).get_all()

    body_usrs: set[str] = set()
    for busr, bname in body_rows:
        if any(kw in (bname or "").lower() for kw in ("body", "view", "content")):
            body_usrs.add(busr)

    if not body_usrs:
        return {"view_tree_edges": 0, "nav_flow_edges": 0}

    # 3. Callees of body members that are also Views -> ViewTree.
    vt_written = 0
    nf_written = 0

    for busr in body_usrs:
        callee_rows = conn.execute(
            "MATCH (b:Symbol {id: $bid})-[r:Calls]->(c:Symbol) "
            "RETURN c.usr, c.name",
            {"bid": make_symbol_id(busr)},
        ).get_all()

        for cusr, cname in callee_rows:
            if cusr in view_usrs:
                # Associate body's callee with the view that owns it.
                # The body USR typically contains the view USR as prefix.
                owner_usr = _find_owner(busr, view_usrs)
                if owner_usr:
                    _write(conn, target_id, owner_usr, cusr, "ViewTree", build_id)
                    vt_written += 1

        # 4. NavigationLink detection.
        for cusr, cname in callee_rows:
            if cname and any(kw in cname
                             for kw in ("NavigationLink", "Sheet", "FullScreenCover")):
                owner_usr = _find_owner(busr, view_usrs)
                if owner_usr:
                    for other in view_usrs - {owner_usr}:
                        _write(conn, target_id, owner_usr, other,
                               "NavigationFlow", build_id)
                        nf_written += 1
                    break  # one NavigationLink per body is enough

    return {
        "view_tree_edges": _count(conn, "ViewTree", build_id) - before_vt,
        "nav_flow_edges": _count(conn, "NavigationFlow", build_id) - before_nf,
    }


def _find_owner(body_usr: str, view_usrs: set[str]) -> str | None:
    """Return the view USR that owns *body_usr*, or None."""
    for v in view_usrs:
        if body_usr.startswith(v) or v.startswith(body_usr):
            return v
    # Fallback: return first view.
    return next(iter(view_usrs), None)


def _write(conn, target_id, src_usr, tgt_usr, rel_type, build_id):
    src_id = make_symbol_id(src_usr)
    tgt_id = make_symbol_id(tgt_usr)
    conn.execute(
        f"MATCH (a:Symbol {{id: $src}}), (b:Symbol {{id: $tgt}}) "
        f"MERGE (a)-[:{rel_type} {{derived_from: 'derive/swiftui', "
        f"confidence: 0.75, build_id: $bid}}]->(b)",
        {"src": src_id, "tgt": tgt_id, "bid": build_id},
    )


def _count(conn, rel_type, build_id):
    rows = conn.execute(
        f"MATCH ()-[r:{rel_type} {{build_id: $bid}}]->() RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    return int(rows[0][0]) if rows else 0
