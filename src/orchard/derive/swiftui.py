"""swiftui_derivation phase.

Builds ViewTree and NavigationFlow edges from struct Symbols using
heuristic name/module matching.  This is a placeholder — real derivation
requires Xcode SwiftUI static analysis (AST walk, body extraction,
view-builder lowering).
"""

from __future__ import annotations

from orchard.normalize.identity import make_symbol_id


def run_swiftui_derivation(conn, target_id: str, build_id: str) -> dict[str, int]:
    """Derive ViewTree and NavigationFlow edges heuristically from struct Symbols.

    Heuristic (placeholder):
      1. Find all ``Symbol`` nodes where ``kind='struct'`` and group by module.
      2. **ViewTree**: within each module, designate the first struct (by name)
         as the "root view" and create ``ViewTree`` edges from it to every other
         struct in the same module.
      3. **NavigationFlow**: for every struct whose name suggests navigation
         (contains "Navigation", "Link", "Nav", or "Router" case-insensitively),
         create a ``NavigationFlow`` edge to the first non-navigation struct
         in the same module.

    All edges are written with ``confidence=0.70`` and
    ``derived_from='derive/swiftui'``.  ``MERGE`` is used so repeated runs
    are idempotent.

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
        ``{"view_tree_edges": N, "nav_flow_edges": N}``
    """
    # ------------------------------------------------------------------
    # 1. Ensure rel tables exist (idempotent).
    # ------------------------------------------------------------------
    for ddl in [
        "CREATE REL TABLE IF NOT EXISTS ViewTree("
        "  FROM Symbol TO Symbol,"
        "  derived_from STRING, confidence DOUBLE, build_id STRING)",
        "CREATE REL TABLE IF NOT EXISTS NavigationFlow("
        "  FROM Symbol TO Symbol,"
        "  derived_from STRING, confidence DOUBLE, build_id STRING)",
    ]:
        conn.execute(ddl)

    # Count edges already present so we can report only new ones.
    before_vt = _count_edges(conn, "ViewTree", build_id)
    before_nf = _count_edges(conn, "NavigationFlow", build_id)

    # ------------------------------------------------------------------
    # 2. Discover struct Symbols and group by module.
    # ------------------------------------------------------------------
    rows = conn.execute(
        "MATCH (s:Symbol {target_id: $tid}) "
        "WHERE s.kind = 'struct' "
        "RETURN s.usr, s.name, s.module",
        {"tid": target_id},
    ).get_all()

    # Group: module_name -> list of (usr, name)
    modules: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        usr, name, mod = row[0], row[1], row[2] or ""
        modules.setdefault(mod, []).append((usr, name))

    if not modules:
        return {"view_tree_edges": 0, "nav_flow_edges": 0}

    # ------------------------------------------------------------------
    # 3. ViewTree: root view → all other structs in the same module.
    # ------------------------------------------------------------------
    for mod_name, structs in modules.items():
        if len(structs) < 2:
            continue
        # Sort alphabetically by name for determinism — the first becomes
        # the "root view".
        sorted_structs = sorted(structs, key=lambda x: x[1])
        root_usr, root_name = sorted_structs[0]
        for child_usr, child_name in sorted_structs[1:]:
            _write_edge(
                conn,
                target_id,
                src_usr=root_usr,
                tgt_usr=child_usr,
                rel_type="ViewTree",
                build_id=build_id,
            )

    # ------------------------------------------------------------------
    # 4. NavigationFlow: navigation structs → destination views.
    # ------------------------------------------------------------------
    NAV_KEYWORDS = ("navigation", "link", "nav", "router")
    for mod_name, structs in modules.items():
        if len(structs) < 2:
            continue
        sorted_structs = sorted(structs, key=lambda x: x[1])
        # Find a default destination (first non-navigation struct).
        destinations = [
            (usr, name)
            for usr, name in sorted_structs
            if not _is_navigation_name(name)
        ]
        if not destinations:
            continue
        dest_usr, dest_name = destinations[0]

        for usr, name in sorted_structs:
            if _is_navigation_name(name) and usr != dest_usr:
                _write_edge(
                    conn,
                    target_id,
                    src_usr=usr,
                    tgt_usr=dest_usr,
                    rel_type="NavigationFlow",
                    build_id=build_id,
                )

    # ------------------------------------------------------------------
    # 5. Compute delta counts.
    # ------------------------------------------------------------------
    after_vt = _count_edges(conn, "ViewTree", build_id)
    after_nf = _count_edges(conn, "NavigationFlow", build_id)

    return {
        "view_tree_edges": after_vt - before_vt,
        "nav_flow_edges": after_nf - before_nf,
    }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _is_navigation_name(name: str) -> bool:
    """Return True if *name* suggests a navigation role."""
    lower = name.lower()
    return any(kw in lower for kw in ("navigation", "link", "nav", "router"))


def _write_edge(
    conn,
    target_id: str,
    src_usr: str,
    tgt_usr: str,
    rel_type: str,
    build_id: str,
) -> None:
    """Write a MERGE edge of *rel_type* between two Symbols."""
    src_id = make_symbol_id(target_id, src_usr)
    tgt_id = make_symbol_id(target_id, tgt_usr)
    conn.execute(
        f"MATCH (a:Symbol {{id: $src}}), (b:Symbol {{id: $tgt}}) "
        f"MERGE (a)-[:{rel_type} {{derived_from: 'derive/swiftui', "
        f"confidence: 0.70, build_id: $bid}}]->(b)",
        {"src": src_id, "tgt": tgt_id, "bid": build_id},
    )


def _count_edges(conn, rel_type: str, build_id: str) -> int:
    """Return the number of *rel_type* edges for the given build_id."""
    rows = conn.execute(
        f"MATCH ()-[r:{rel_type} {{build_id: $bid}}]->() "
        "RETURN count(r)",
        {"bid": build_id},
    ).get_all()
    return int(rows[0][0]) if rows else 0
