"""architecture_derivation phase.

Builds Module DEPENDS_ON edges from cross-module Calls and References,
and detects dependency cycles in the module graph.
"""

from __future__ import annotations


def run_architecture_derivation(conn, scope_id: str, build_id: str) -> dict[str, int]:
    """Build Module DEPENDS_ON edges from cross-module Calls/References and detect cycles.

    Queries for:
      1. Cross-module Calls:  Symbol A calls Symbol B, A.module != B.module.
      2. Cross-module References: Symbol A references Symbol B, A.module != B.module.

    For each pair (caller_module, callee_module) or (ref_module, referenced_module),
    a DEPENDS_ON edge is written between the corresponding Module nodes.  Uses MERGE
    so repeated runs with the same data are idempotent.

    After writing edges, a DFS-based cycle detector runs over the Module DEPENDS_ON
    graph and reports how many cycles were found.

    Parameters
    ----------
    conn
        Open Ladybug connection.
    scope_id
        Legacy scope label retained for API stability. The current
        implementation derives module dependencies from the whole build scope.
    build_id
        The build snapshot identifier (stored on DEPENDS_ON edges for traceability).

    Returns
    -------
    dict
        ``{"module_deps": N, "cycles_detected": N}`` where *module_deps* counts
        the number of unique (source_module, target_module) pairs written.
    """
    # ------------------------------------------------------------------
    # 1. Ensure DEPENDS_ON rel table exists (idempotent).
    # ------------------------------------------------------------------
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS DependsOn("
        "  FROM Module TO Module,"
        "  source STRING,"
        "  build_id STRING"
        ")"
    )

    # Track module pairs already known so we only write new ones.
    existing_pairs: set[tuple[str, str]] = set()
    # Also track pairs we write in this call for cycle detection.
    written_pairs: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # 2. Discover cross-module Calls edges.
    # ------------------------------------------------------------------
    calls_rows = conn.execute(
        "MATCH (a:Symbol)-[r:Calls]->(b:Symbol) "
        "WHERE a.module IS NOT NULL AND b.module IS NOT NULL "
        "  AND a.module <> b.module "
        "RETURN DISTINCT a.module, b.module",
    ).get_all()

    for row in calls_rows:
        src_mod = row[0]
        tgt_mod = row[1]
        pair = (src_mod, tgt_mod)
        existing_pairs.add(pair)

    # ------------------------------------------------------------------
    # 3. Discover cross-module References edges.
    # ------------------------------------------------------------------
    refs_rows = conn.execute(
        "MATCH (a:Symbol)-[r:References]->(b:Symbol) "
        "WHERE a.module IS NOT NULL AND b.module IS NOT NULL "
        "  AND a.module <> b.module "
        "RETURN DISTINCT a.module, b.module",
    ).get_all()

    union_pairs: set[tuple[str, str]] = set(existing_pairs)
    for row in refs_rows:
        src_mod = row[0]
        tgt_mod = row[1]
        union_pairs.add((src_mod, tgt_mod))

    if not union_pairs:
        return {"module_deps": 0, "cycles_detected": 0}

    # ------------------------------------------------------------------
    # 4. Upsert Module nodes + MERGE DependsOn edges.
    # ------------------------------------------------------------------
    for src_mod, tgt_mod in sorted(union_pairs):
        # Ensure Module nodes exist.
        for mod_name in (src_mod, tgt_mod):
            conn.execute(
                "MERGE (m:Module {name: $name})",
                {"name": mod_name},
            )
        # Write the dependency edge.
        conn.execute(
            "MATCH (src:Module {name: $src}), (tgt:Module {name: $tgt}) "
            "MERGE (src)-[:DependsOn {source: $source, build_id: $bid}]->(tgt)",
            {
                "src": src_mod,
                "tgt": tgt_mod,
                "source": "derive/architecture",
                "bid": build_id,
            },
        )
        written_pairs.add((src_mod, tgt_mod))

    # Count all unique DependsOn edges for this build_id (not just new ones).
    deps_count = len(written_pairs)

    # ------------------------------------------------------------------
    # 5. Cycle detection via DFS over the module dependency graph.
    # ------------------------------------------------------------------
    # Build adjacency list from all DependsOn edges.
    all_deps = conn.execute(
        "MATCH (src:Module)-[:DependsOn]->(tgt:Module) "
        "RETURN src.name, tgt.name"
    ).get_all()

    adj: dict[str, list[str]] = {}
    for row in all_deps:
        s, t = row[0], row[1]
        adj.setdefault(s, []).append(t)

    cycles_found = 0
    visited: set[str] = set()
    recursion_stack: set[str] = set()

    def dfs(node: str) -> bool:
        """Return True if a cycle is found starting from *node*."""
        nonlocal cycles_found
        visited.add(node)
        recursion_stack.add(node)
        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor):
                    cycles_found += 1
            elif neighbor in recursion_stack:
                # Back edge found — this is a cycle.
                cycles_found += 1
        recursion_stack.discard(node)
        return False

    for module_name in list(adj.keys()):
        if module_name not in visited:
            # Reset recursion stack per connected component so we don't
            # double-count cross-component back edges.
            dfs(module_name)

    return {
        "module_deps": deps_count,
        "cycles_detected": cycles_found,
    }
