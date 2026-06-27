"""get_module_graph — query Module nodes and their DependsOn edges."""

from __future__ import annotations

from dataclasses import dataclass, field

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for


@dataclass
class ModuleGraphRequest(BaseToolRequest):
    """Request for querying the module dependency graph.

    Attributes:
        module_filter: Optional substring filter on module name.
        include_deps: Whether to include DependsOn edges in the response
            (default True).
    """

    module_filter: str | None = None
    include_deps: bool = True


def get_module_graph(conn, req: ModuleGraphRequest) -> BaseToolResponse:
    """Return Module nodes (optionally filtered) and their DependsOn edges.

    When *include_deps* is True (the default), the response includes both
    a list of modules and the directed dependency edges between them.
    When False, only the module list is returned.

    Parameters
    ----------
    conn
        An open Ladybug connection.
    req
        The module graph request.

    Returns
    -------
    BaseToolResponse
        ``data = {"modules": [...], "edges": [...]}``
    """
    mod_filter = req.module_filter or ""

    # Gather Module nodes.
    if mod_filter:
        modules_rows = conn.execute(
            "MATCH (m:Module) WHERE m.name CONTAINS $filt "
            "RETURN m.name, m.language",
            {"filt": mod_filter},
        ).get_all()
    else:
        modules_rows = conn.execute(
            "MATCH (m:Module) RETURN m.name, m.language"
        ).get_all()

    modules = [
        {"name": r[0], "language": r[1] or ""}
        for r in modules_rows
    ]

    # Gather DependsOn edges.
    edges: list[dict] = []
    if req.include_deps:
        if mod_filter:
            # Filter edges where either endpoint matches the module filter.
            edges_rows = conn.execute(
                "MATCH (src:Module)-[d:DependsOn]->(tgt:Module) "
                "WHERE src.name CONTAINS $filt OR tgt.name CONTAINS $filt "
                "RETURN src.name, tgt.name, d.source, d.build_id",
                {"filt": mod_filter},
            ).get_all()
        else:
            edges_rows = conn.execute(
                "MATCH (src:Module)-[d:DependsOn]->(tgt:Module) "
                "RETURN src.name, tgt.name, d.source, d.build_id"
            ).get_all()
        edges = [
            {
                "source_module": r[0],
                "target_module": r[1],
                "derivation_source": r[2] or "",
                "build_id": r[3] or "",
            }
            for r in edges_rows
        ]

    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    return BaseToolResponse(
        data={
            "modules": modules,
            "edges": edges,
            "module_count": len(modules),
            "edge_count": len(edges),
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["architecture_derivation"],
        open_gaps=[] if (modules or edges) else ["no modules found"],
    )
