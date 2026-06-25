"""impact_analysis — multi-hop impact traversal with depth grouping and risk scoring.

Traverses incoming Calls, References, Implements, and BridgesTo edges (reverse
direction) to find all dependents of a queried symbol, grouped by depth level.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.mcp.handlers.impact_policy import ImpactTraversalPolicy
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class ImpactRequest(BaseToolRequest):
    """Request for impact_analysis traversal.

    Attributes:
        usr: The USR (Unified Symbol Resolution) string of the queried symbol.
        target_id: The build target identifier used to disambiguate symbols.
    """

    usr: str = ""
    target_id: str | None = None


def _risk_level(d1_count: int, has_bridge: bool, freshness_ok: bool) -> str:
    """Compute the risk level for the impact analysis result.

    Parameters
    ----------
    d1_count:
        Number of direct (depth-1) dependents.
    has_bridge:
        True if any d1 dependent was reached via a BridgesTo edge.
    freshness_ok:
        True if the build snapshot freshness status is "fresh".

    Returns
    -------
    str
        One of "low", "medium", "high", or "critical".
    """
    if not freshness_ok:
        return "critical"
    if d1_count >= 10 or (has_bridge and d1_count >= 4):
        return "high"
    if 4 <= d1_count <= 9:
        return "medium"
    return "low"


def impact_analysis(conn, req: ImpactRequest) -> BaseToolResponse:
    """Perform multi-hop impact analysis traversal from a queried symbol.

    Traverses incoming edges (reverse direction: ``(next)-[:Calls]->(current)``)
    iteratively per depth level, following relation types defined by the default
    :class:`ImpactTraversalPolicy`. Low-confidence BridgesTo edges (confidence
    < 0.70) are excluded from default traversal.

    Parameters
    ----------
    conn:
        An open Ladybug connection.
    req:
        The impact analysis request carrying usr, target_id, and build_id.

    Returns
    -------
    BaseToolResponse
        Response with ``data = {"by_depth": ..., "risk": ...}``, freshness,
        build_id, evidence_sources, and open_gaps.
    """
    policy = ImpactTraversalPolicy()
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)
    max_depth = min(req.max_depth or 5, policy.max_depth)

    # Build per-depth result dict.
    depths: dict[str, list[dict]] = {}

    # BFS: start from the queried symbol, expand via incoming edges.
    current_ids = {sym_id}
    visited_ids = {sym_id}

    for depth in range(1, max_depth + 1):
        next_ids: set[str] = set()
        ids_list = list(current_ids)
        for rel_type in policy.effective_relation_types():
            if rel_type == "BridgesTo" and not policy.include_low_confidence:
                conf_filter = " AND r.confidence >= 0.70"
            else:
                conf_filter = ""

            # UNWIND batch: one query per rel_type, not per cid.
            rows = conn.execute(
                f"UNWIND $ids AS cid "
                f"MATCH (next:Symbol)-[r:{rel_type}]->(current:Symbol {{id: cid}}) "
                f"WHERE next.id <> cid{conf_filter} "
                "RETURN next.id, next.usr, next.name, next.module, "
                "next.language, next.kind",
                {"ids": ids_list},
            ).get_all()
            for row in rows:
                next_id = row[0]
                next_usr = row[1]
                if next_id not in visited_ids:
                    visited_ids.add(next_id)
                    next_ids.add(next_id)
                    depths.setdefault(f"d{depth}", []).append({
                        "usr": next_usr,
                        "name": row[2],
                        "module": row[3],
                        "language": row[4],
                        "kind": row[5],
                        "reached_via": rel_type,
                    })
        if not next_ids:
            break
        current_ids = next_ids

    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    d1 = depths.get("d1", [])
    # Use actual edge-type tracking instead of language-proxy heuristic.
    # A dependent reached via BridgesTo indicates cross-language impact.
    has_bridge = any(
        d.get("reached_via") == "BridgesTo" for d in d1
    )
    risk = _risk_level(len(d1), has_bridge, freshness_status == "fresh")

    open_gaps: list[str] = []
    if not depths:
        open_gaps.append("no dependents found")

    return BaseToolResponse(
        data={"by_depth": depths, "risk": risk},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation", "cross_language_bridge_recovery"],
        open_gaps=open_gaps,
    )
