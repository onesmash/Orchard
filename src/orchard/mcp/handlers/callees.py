from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class CalleeRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def find_callees(conn, req: CalleeRequest) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)
    rows = conn.execute(
        "MATCH (src:Symbol {id: $id})-[:Calls]->(callee:Symbol) "
        "RETURN callee.usr, callee.name, callee.module",
        {"id": sym_id},
    ).get_all()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [{"usr": r[0], "name": r[1], "module": r[2], "depth": 1} for r in rows]
    return BaseToolResponse(
        data=data,
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callees found"],
    )
