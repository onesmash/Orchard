"""Type hierarchy handler for MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for


@dataclass
class TypeHierarchyRequest(BaseToolRequest):
    """Request for type hierarchy information."""

    usr: str = ""
    target_id: str | None = None


def get_type_hierarchy(conn, req: TypeHierarchyRequest) -> BaseToolResponse:
    """Get the type hierarchy for a symbol (parents, protocols, children).

    Parameters
    ----------
    conn
        An open Ladybug connection.
    req : TypeHierarchyRequest
        Request containing the symbol USR and target ID.

    Returns
    -------
    BaseToolResponse
        Response containing parents, protocols, and children lists.
    """
    target_id = req.target_id or ""
    sym_id = f"{target_id}:{req.usr}"
    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    parents = conn.execute(
        "MATCH (s:Symbol {id: $id})-[:Inherits]->(p:Symbol) RETURN p.usr, p.name, p.module",
        {"id": sym_id},
    ).get_all()

    protocols = conn.execute(
        "MATCH (s:Symbol {id: $id})-[:ConformsTo]->(p:Symbol) RETURN p.usr, p.name, p.module",
        {"id": sym_id},
    ).get_all()

    children = conn.execute(
        "MATCH (c:Symbol)-[:Inherits]->(s:Symbol {id: $id}) RETURN c.usr, c.name, c.module",
        {"id": sym_id},
    ).get_all()

    def to_list(rows):
        return [{"usr": r[0], "name": r[1], "module": r[2]} for r in rows]

    return BaseToolResponse(
        data={
            "parents": to_list(parents),
            "protocols": to_list(protocols),
            "children": to_list(children),
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["swift_symbolgraph_ingest"],
        open_gaps=[],
    )
