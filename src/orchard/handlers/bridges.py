"""get_cross_language_bridges — query BridgesTo edges for a symbol."""

from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.normalize.identity import make_symbol_id
from orchard.validation.freshness import freshness_for


@dataclass
class BridgesRequest(BaseToolRequest):
    usr: str = ""


def get_cross_language_bridges(conn, req: BridgesRequest) -> BaseToolResponse:
    """Return all BridgesTo edges (both directions) for a symbol.

    Edges are returned with ``bridge_kind``, ``confidence``, ``provenance``,
    the cross-language name fields, and the remote symbol's USR (+ name +
    language).
    """
    sym_id = make_symbol_id(req.usr)

    rows = conn.execute(
        "MATCH (s:Symbol {id: $id})-[r:BridgesTo]-(other:Symbol) "
        "RETURN r.bridge_kind, r.confidence, r.provenance, "
        "r.clang_name, r.swift_name, r.definition_language, "
        "other.usr, other.name, other.language",
        {"id": sym_id},
    ).get_all()

    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    data = [
        {
            "bridge_kind": r[0],
            "confidence": float(r[1]) if r[1] is not None else 0.0,
            "provenance": r[2] or "",
            "clang_name": r[3] or "",
            "swift_name": r[4] or "",
            "definition_language": r[5] or "",
            "target_usr": r[6],
            "target_name": r[7],
            "target_language": r[8],
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
