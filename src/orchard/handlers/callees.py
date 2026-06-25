from dataclasses import dataclass
from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.query.lookup import GraphLookup


@dataclass
class CalleeRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def find_callees(conn, req: CalleeRequest) -> BaseToolResponse:
    g = GraphLookup(conn)
    data = g.callees_of(req.usr, req.target_id or "")
    _, status = g.freshness(req.build_id or "")
    return BaseToolResponse(
        data=[{**d, "depth": 1} for d in data],
        freshness=status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callees found"],
    )
