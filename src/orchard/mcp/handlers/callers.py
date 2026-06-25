from dataclasses import dataclass
from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class CallerRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def _resolve_owner(conn, usr: str) -> dict | None:
    """Walk Contains edges up to find the class/struct owning a symbol."""
    rows = conn.execute(
        "MATCH (s:Symbol {usr: $usr})<-[:Contains]-(owner:Symbol) "
        "WHERE owner.kind IN ['class','struct','enum','protocol','extension'] "
        "RETURN owner.usr, owner.name, owner.kind, owner.module LIMIT 1",
        {"usr": usr},
    ).get_all()
    if rows:
        return {"usr": rows[0][0], "name": rows[0][1], "kind": rows[0][2], "module": rows[0][3]}
    return None


def find_callers(conn, req: CallerRequest) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)
    rows = conn.execute(
        "MATCH (caller:Symbol)-[:Calls]->(target:Symbol {id: $id}) "
        "RETURN DISTINCT caller.usr, caller.name, caller.module, caller.kind, caller.language",
        {"id": sym_id},
    ).get_all()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [
        {
            "usr": r[0], "name": r[1], "module": r[2],
            "kind": r[3], "language": r[4], "depth": 1,
            "owner": _resolve_owner(conn, r[0]),
        }
        for r in rows
    ]
    return BaseToolResponse(
        data=data,
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callers found"],
    )
