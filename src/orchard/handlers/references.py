"""find_references — return all source locations referencing a symbol."""
from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class ReferencesRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def find_references(conn, req: ReferencesRequest) -> BaseToolResponse:
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)
    rows = conn.execute(
        "MATCH (s:Symbol {id: $id})-[r:Calls]->(ref:Symbol) "
        "RETURN s.usr, s.name, s.module, ref.usr, ref.name, ref.kind",
        {"id": sym_id},
    ).get_all()
    # Also check: who calls this symbol (incoming references).
    inc_rows = conn.execute(
        "MATCH (caller:Symbol)-[r:Calls]->(ref:Symbol {id: $id}) "
        "RETURN caller.usr, caller.name, caller.module, ref.usr, ref.name, ref.kind",
        {"id": sym_id},
    ).get_all()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    outgoing = [
        {"caller_usr": r[0], "caller_name": r[1], "caller_module": r[2],
         "target_usr": r[3], "target_name": r[4], "target_kind": r[5]}
        for r in rows
    ]
    incoming = [
        {"caller_usr": r[0], "caller_name": r[1], "caller_module": r[2],
         "target_usr": r[3], "target_name": r[4], "target_kind": r[5]}
        for r in inc_rows
    ]
    return BaseToolResponse(
        data={"outgoing": outgoing, "incoming": incoming},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if (outgoing or incoming) else ["no references found"],
    )
