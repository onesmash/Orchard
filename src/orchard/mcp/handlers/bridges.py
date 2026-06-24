"""get_cross_language_bridges — query BridgesTo edges for a symbol."""

from dataclasses import dataclass

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class BridgesRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None


def get_cross_language_bridges(conn, req: BridgesRequest) -> BaseToolResponse:
    """Return all BridgesTo edges (both directions) for a symbol.

    Edges are returned with ``bridge_kind``, ``confidence``, ``provenance``,
    and the remote symbol's USR (+ name + language).
    """
    target_id = req.target_id or ""
    sym_id = make_symbol_id(target_id, req.usr)

    rows = conn.execute(
        "MATCH (s:Symbol {id: $id})-[r:BridgesTo]-(other:Symbol) "
        "RETURN r.bridge_kind, r.confidence, r.provenance, "
        "other.usr, other.name, other.language",
        {"id": sym_id},
    ).get_all()

    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [
        {
            "bridge_kind": r[0],
            "confidence": float(r[1]) if r[1] is not None else 1.0,
            "provenance": r[2] or "",
            "target_usr": r[3],
            "target_name": r[4],
            "target_language": r[5],
        }
        for r in rows
    ]

    return BaseToolResponse(
        data=data,
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["cross_language_bridge_recovery"],
        open_gaps=[] if data else ["no bridges found for this symbol"],
    )
