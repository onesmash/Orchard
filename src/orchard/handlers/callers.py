"""find_callers handler — resolves callers of a symbol.

When the target symbol is a class, struct, enum, or protocol this handler
auto-expands: it enumerates the type's methods (via :Contains edges) and
collects callers for each method, annotating every caller with ``via_method``
so the consumer knows which method brought the caller into the result set.

Callers are deduplicated by USR (first occurrence wins) to avoid repeating
the same caller when it invokes multiple methods of the target type.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.query.lookup import GraphLookup, is_framework_callback

# Kinds for which we auto-expand to method-level callers.
_AUTO_EXPAND_KINDS = frozenset({"class", "struct", "enum", "protocol"})


@dataclass
class CallerRequest(BaseToolRequest):
    usr: str = ""
    target_id: str | None = None
    depth: int = 1
    relation_types: list[str] = field(default_factory=lambda: ["Calls"])


def find_callers(conn, req: CallerRequest) -> BaseToolResponse:
    """Return all callers of the symbol identified by *req.usr*.

    If the symbol is a type (class / struct / enum / protocol) the handler
    auto-expands to the type's methods and returns every method-caller,
    annotated with ``via_method``.
    """
    g = GraphLookup(conn)
    target_id = req.target_id or ""

    # Resolve the symbol to decide whether auto-expand applies.
    sym = g.symbol(req.usr, target_id)
    if sym is not None and sym.get("kind") in _AUTO_EXPAND_KINDS:
        # ── auto-expand: enumerate methods, collect their callers ──────────
        methods = g.methods_of(req.usr, target_id)
        seen_caller_usrs: set[str] = set()
        all_callers: list[dict] = []

        for method in methods:
            for caller in g.callers_of(method["usr"], target_id, req.relation_types):
                usr = caller["usr"]
                if usr not in seen_caller_usrs:
                    seen_caller_usrs.add(usr)
                    caller["via_method"] = method["name"]
                    caller["depth"] = 1
                    all_callers.append(caller)

        _, status = g.freshness(req.build_id or "")
        open_gaps = _build_open_gaps(g, all_callers, methods, req.usr, target_id)
        return BaseToolResponse(
            data=all_callers,
            freshness=status,
            build_id=req.build_id,
            evidence_sources=["call_graph_derivation"],
            open_gaps=open_gaps,
        )

    # ── single-symbol path (existing behaviour) ────────────────────────
    if req.depth > 1:
        data = g.callers_of_depth(req.usr, target_id, req.depth, req.relation_types)
    else:
        data = g.callers_of(req.usr, target_id, req.relation_types)
        data = [{**d, "depth": 1} for d in data]
    _, status = g.freshness(req.build_id or "")
    sym_name = g.symbol(req.usr, target_id)
    open_gaps = _build_open_gaps_single(data, sym_name)
    return BaseToolResponse(
        data=data,
        freshness=status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=open_gaps,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRAMEWORK_GAP_MSG = (
    "No callers found — likely called by system framework (UIKit/AppKit). "
    "Use reverse tracing via find_callees to see what this method calls."
)


def _build_open_gaps(
    g: GraphLookup,
    all_callers: list[dict],
    methods: list[dict],
    usr: str,
    target_id: str,
) -> list[str]:
    """Build the open_gaps list for the auto-expand path.

    When no callers were found for any method, check whether the methods
    themselves look like framework callbacks and annotate accordingly.
    """
    if all_callers:
        return []
    # No callers at all — check if the class-level USR or any method
    # matches a framework callback pattern.
    if any(is_framework_callback(m["name"]) for m in methods):
        return [_FRAMEWORK_GAP_MSG]
    return ["no callers found"]


def _build_open_gaps_single(
    data: list[dict],
    sym: dict | None,
) -> list[str]:
    """Build the open_gaps list for the single-symbol path."""
    if data:
        return []
    if sym is not None and is_framework_callback(sym["name"]):
        return [_FRAMEWORK_GAP_MSG]
    return ["no callers found"]
