"""find_references — return all source locations referencing a symbol.

Returns both incoming (callers) and outgoing (callees) references.
Each edge carries ``confidence``, ``provenance``, and ``owner`` metadata.
ObjC callees also carry ``semantic_role`` (notification_observer,
delegate_setter, framework_callback, ...) inline.
"""
from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.query.lookup import GraphLookup


@dataclass
class ReferencesRequest(BaseToolRequest):
    usr: str = ""


def find_references(conn, req: ReferencesRequest) -> BaseToolResponse:
    """Return incoming (callers) and outgoing (callees) references.

    Uses GraphLookup so edges automatically carry ``confidence``,
    ``provenance``, ``owner``, and (for ObjC callees) ``semantic_role``.
    """
    g = GraphLookup(conn)

    outgoing = g.callees_of(req.usr)
    outgoing = [{**d, "depth": 1} for d in outgoing]

    incoming = g.callers_of(req.usr)
    incoming = [{**d, "depth": 1} for d in incoming]

    _, freshness_status = g.freshness(req.build_id or "")

    return BaseToolResponse(
        data={"outgoing": outgoing, "incoming": incoming},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if (outgoing or incoming) else ["no references found"],
    )
