"""impact_analysis — multi-hop impact traversal with depth grouping and risk scoring.

Traverses incoming Calls, References, Implements, and BridgesTo edges (reverse
direction) to find all dependents of a queried symbol, grouped by depth level.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.handlers.impact_policy import ImpactTraversalPolicy
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class ImpactRequest(BaseToolRequest):
    """Request for impact_analysis traversal.

    Attributes:
        usr: The USR (Unified Symbol Resolution) string of the queried symbol.
    """

    usr: str = ""


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


def _subtype_closure(conn, usr: str, max_depth: int = 20) -> set[str]:
    """Return all USRs that are subtypes or conformers of *usr*.

    Walks Inherits:FROM, ConformsTo:FROM, and Extends:FROM edges
    recursively with a visited guard and depth limit.
    """
    visited: set[str] = set()
    frontier = {usr}
    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        f_list = list(frontier)
        for rel_type in ("Inherits", "ConformsTo", "Extends", "Implements"):
            rows = conn.execute(
                f"UNWIND $ids AS uid "
                f"MATCH (child:Symbol)-[:{rel_type}]->(parent:Symbol {{usr: uid}}) "
                f"WHERE child.usr <> uid "
                f"RETURN DISTINCT child.usr",
                {"ids": f_list},
            ).get_all()
            for row in rows:
                child_usr = row[0]
                if child_usr not in visited and child_usr != usr:
                    next_frontier.add(child_usr)
                    visited.add(child_usr)
        frontier = next_frontier
    return visited


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
        The impact analysis request carrying usr and build_id.

    Returns
    -------
    BaseToolResponse
        Response with ``data = {"by_depth": ..., "risk": ...}``, freshness,
        build_id, evidence_sources, and open_gaps.
    """
    policy = ImpactTraversalPolicy()
    sym_id = make_symbol_id(req.usr)
    max_depth = min(req.max_depth or 5, policy.max_depth)

    # Build per-depth result dict.
    depths: dict[str, list[dict]] = {}

    # Seed subtype closure BEFORE BFS so conformers reached via both closure
    # and Calls edges are not double-counted.
    subtype_usrs = _subtype_closure(conn, req.usr, max_depth=policy.max_depth)
    # Look up symbol metadata for subtypes and add to d1.
    d1_subtypes: list[dict] = []
    for sub_usr in subtype_usrs:
        s_rows = conn.execute(
            "MATCH (s:Symbol {usr: $usr}) "
            "RETURN s.id, s.usr, s.name, s.module, s.language, s.kind LIMIT 1",
            {"usr": sub_usr},
        ).get_all()
        for row in s_rows:
            d1_subtypes.append({
                "usr": row[1], "name": row[2], "module": row[3],
                "language": row[4], "kind": row[5],
                "reached_via": "subtype_closure",
            })

    # BFS: start from the queried symbol, expand via incoming edges.
    current_ids = {sym_id}
    visited_ids = {sym_id}
    # Seed visited with subtype symbol IDs so BFS doesn't re-add them.
    for entry in d1_subtypes:
        visited_ids.add(make_symbol_id(entry["usr"]))
    if d1_subtypes:
        depths.setdefault("d1", []).extend(d1_subtypes)

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

    # Freshness annotation: check d1 dependents' file_path mtime against the
    # build snapshot's created_at.  Annotate via open_gaps (do NOT filter —
    # filtering would shrink d1_count and misleadingly lower risk).
    # Empty file_path or no build snapshot → default up-to-date.
    from orchard.validation.freshness import IndexOutOfDateChecker, IndexCheckLevel, SymbolLocation
    from datetime import datetime
    index_ts: float | None = None
    if req.build_id:
        snap_rows = conn.execute(
            "MATCH (b:BuildSnapshot {id: $id}) RETURN b.created_at LIMIT 1",
            {"id": req.build_id},
        ).get_all()
        if snap_rows and snap_rows[0][0]:
            try:
                index_ts = datetime.fromisoformat(snap_rows[0][0]).timestamp()
            except (ValueError, OSError):
                index_ts = None
    if index_ts is not None:
        checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
        d1_usrs = [d["usr"] for d in d1]
        if d1_usrs:
            fp_rows = conn.execute(
                "UNWIND $usrs AS u MATCH (s:Symbol {usr: u}) RETURN s.usr, s.file_path",
                {"usrs": d1_usrs},
            ).get_all()
            for row in fp_rows:
                usr, fp = row[0], row[1]
                if not fp:
                    continue
                try:
                    loc = SymbolLocation(path=fp, timestamp=index_ts)
                    if not checker.is_up_to_date(loc):
                        open_gaps.append(f"dependent '{usr}' in file '{fp}' may be stale")
                except OSError:
                    open_gaps.append(f"dependent '{usr}' references missing file '{fp}'")

    return BaseToolResponse(
        data={"by_depth": depths, "risk": risk},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation", "cross_language_bridge_recovery"],
        open_gaps=open_gaps,
    )
