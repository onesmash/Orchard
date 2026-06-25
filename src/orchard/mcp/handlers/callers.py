from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.query.lookup import GraphLookup


@dataclass
class CallerRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def find_callers(conn, req: CallerRequest) -> BaseToolResponse:
    g = GraphLookup(conn)
    data = g.callers_of(req.usr, req.target_id or "")
    _, status = g.freshness(req.build_id or "")
    return BaseToolResponse(
        data=[{**d, "depth": 1} for d in data],
        freshness=status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callers found"],
    )
