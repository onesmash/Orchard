"""MCP handler for retrieving symbol context from the semantic graph."""

from __future__ import annotations

from dataclasses import dataclass

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for


@dataclass
class SymbolContextRequest(BaseToolRequest):
    """Request for symbol context information."""

    usr: str = ""
    target_id: str | None = None


def get_symbol_context(conn, req: SymbolContextRequest) -> BaseToolResponse:
    """Retrieve symbol context from the semantic graph.

    Parameters
    ----------
    conn
        An open Ladybug connection.
    req : SymbolContextRequest
        The request containing usr, target_id, and build_id.

    Returns
    -------
    BaseToolResponse
        A response containing symbol data, freshness status, and metadata.
    """
    target_id = req.target_id or ""
    sym_id = f"{target_id}:{req.usr}" if target_id else req.usr
    rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) "
        "RETURN s.name, s.language, s.kind, s.module, s.file_path, "
        "s.signature, s.access_level, s.origin",
        {"id": sym_id},
    ).get_all()
    _, freshness_status = freshness_for(conn, req.build_id or "", {})
    if not rows:
        return BaseToolResponse(
            data=None,
            freshness=freshness_status,
            build_id=req.build_id,
            open_gaps=[f"symbol '{req.usr}' not found in target '{target_id}'"],
            evidence_sources=[],
        )
    row = rows[0]
    return BaseToolResponse(
        data={
            "name": row[0],
            "language": row[1],
            "kind": row[2],
            "module": row[3],
            "file_path": row[4],
            "signature": row[5],
            "access_level": row[6],
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=[row[7] or "unknown"],
        open_gaps=[],
    )
