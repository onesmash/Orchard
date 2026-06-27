"""find_callees handler — resolves callees of a symbol.

When the target symbol is a class, struct, enum, or protocol this handler
auto-expands: it enumerates the type's methods (via :Contains edges) and
collects callees for each method.  Unlike the callers handler, callees are
**grouped by callee USR** and each result carries a ``calling_methods`` list
showing which of the type's methods call it.

To keep query volume bounded for types with many methods, only the first 50
methods (ordered by name) are expanded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.query.lookup import GraphLookup

# Kinds for which we auto-expand to method-level callees.
_AUTO_EXPAND_KINDS = frozenset({"class", "struct", "enum", "protocol"})

# Upper bound on the number of methods expanded to avoid excessive DB queries.
_MAX_METHODS_FOR_AUTO_EXPAND = 50


@dataclass
class CalleeRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None
    depth: int = 1
    relation_types: list[str] = field(default_factory=lambda: ["Calls"])


def find_callees(conn, req: CalleeRequest) -> BaseToolResponse:
    """Return all callees of the symbol identified by *req.usr*.

    For type symbols the handler auto-expands to the type's methods and
    groups results by callee USR, aggregating ``calling_methods``.
    """
    g = GraphLookup(conn)
    target_id = req.target_id or ""

    # Resolve the symbol to decide whether auto-expand applies.
    sym = g.symbol(req.usr, target_id)
    if sym is not None and sym.get("kind") in _AUTO_EXPAND_KINDS:
        # ── auto-expand: enumerate methods, collect their callees ─────────
        methods = g.methods_of(req.usr, target_id)
        methods = methods[:_MAX_METHODS_FOR_AUTO_EXPAND]

        # Group callees by USR, accumulating calling methods.
        callee_map: dict[str, dict] = {}
        for method in methods:
            for callee in g.callees_of(method["usr"], target_id, req.relation_types):
                key = callee["usr"]
                if key not in callee_map:
                    callee_map[key] = {
                        "usr": callee["usr"],
                        "name": callee["name"],
                        "module": callee["module"],
                        "kind": callee["kind"],
                        "language": callee["language"],
                        "file_path": callee.get("file_path", ""),
                        "line": callee.get("line"),
                        "col": callee.get("col"),
                        "reason": callee["reason"],
                        "owner": None,
                        "depth": 1,
                        "calling_methods": [],
                    }
                # Append calling method if not already recorded for this callee.
                if method["name"] not in callee_map[key]["calling_methods"]:
                    callee_map[key]["calling_methods"].append(method["name"])

        # Resolve owner for each unique callee.
        for callee_entry in callee_map.values():
            callee_entry["owner"] = g.owner_of(callee_entry["usr"])

        all_callees = list(callee_map.values())
        _, status = g.freshness(req.build_id or "")
        return BaseToolResponse(
            data=all_callees,
            freshness=status,
            build_id=req.build_id,
            evidence_sources=["call_graph_derivation"],
            open_gaps=[] if all_callees else ["no callees found"],
        )

    # ── single-symbol path (existing behaviour) ────────────────────────
    if req.depth > 1:
        data = g.callees_of_depth(req.usr, target_id, req.depth, req.relation_types)
    else:
        data = g.callees_of(req.usr, target_id, req.relation_types)
        data = [{**d, "depth": 1} for d in data]
    _, status = g.freshness(req.build_id or "")
    return BaseToolResponse(
        data=data,
        freshness=status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=[] if data else ["no callees found"],
    )
