"""find_navigation_flow — query NavigationFlow edges from the semantic graph."""

from __future__ import annotations

from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for


@dataclass
class NavigationFlowRequest(BaseToolRequest):
    """Request for querying SwiftUI navigation flows.

    Attributes:
        module: Optional module filter (substring match on Symbol.module).
        build_id: Optional build snapshot identifier for freshness check.
    """

    module: str | None = None


def find_navigation_flow(conn, req: NavigationFlowRequest) -> BaseToolResponse:
    """Return NavigationFlow edges (navigation source -> destination view).

    Each edge includes ``source_usr``, ``source_name``, ``target_usr``,
    ``target_name``, ``confidence``, ``derived_from``, and ``build_id``.

    Parameters
    ----------
    conn
        An open Ladybug connection.
    req
        The navigation flow query request.

    Returns
    -------
    BaseToolResponse
        ``data`` contains the list of NavigationFlow edges plus counts.
    """
    mod_filter = (req.module or "").strip()

    if mod_filter:
        rows = conn.execute(
            "MATCH (a:Symbol)-[r:NavigationFlow]->(b:Symbol) "
            "WHERE a.module CONTAINS $filt OR b.module CONTAINS $filt "
            "RETURN a.usr, a.name, a.module, b.usr, b.name, b.module, "
            "r.confidence, r.derived_from, r.build_id",
            {"filt": mod_filter},
        ).get_all()
    else:
        rows = conn.execute(
            "MATCH (a:Symbol)-[r:NavigationFlow]->(b:Symbol) "
            "RETURN a.usr, a.name, a.module, b.usr, b.name, b.module, "
            "r.confidence, r.derived_from, r.build_id"
        ).get_all()

    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    data = [
        {
            "source_usr": r[0],
            "source_name": r[1],
            "source_module": r[2],
            "target_usr": r[3],
            "target_name": r[4],
            "target_module": r[5],
            "confidence": float(r[6]) if r[6] is not None else 1.0,
            "derived_from": r[7] or "",
            "build_id": r[8] or "",
        }
        for r in rows
    ]

    return BaseToolResponse(
        data={
            "nav_flow_edges": data,
            "edge_count": len(data),
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["swiftui_derivation"],
        open_gaps=[] if data else ["no NavigationFlow edges found"],
    )
