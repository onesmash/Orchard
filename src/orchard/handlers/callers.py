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
    depth: int = 1
    relation_types: list[str] = field(default_factory=lambda: ["Calls"])
    include_inferred: bool = False


def find_callers(conn, req: CallerRequest) -> BaseToolResponse:
    """Return all callers of the symbol identified by *req.usr*.

    If the symbol is a type (class / struct / enum / protocol) the handler
    auto-expands to the type's methods and returns every method-caller,
    annotated with ``via_method``.
    """
    g = GraphLookup(conn)

    # Resolve the symbol to decide whether auto-expand applies.
    sym = g.symbol(req.usr)
    if sym is not None and sym.get("kind") in _AUTO_EXPAND_KINDS:
        # ── auto-expand: enumerate methods, collect their callers ──────────
        methods = g.methods_of(req.usr)
        seen_caller_usrs: set[str] = set()
        all_callers: list[dict] = []

        for method in methods:
            for caller in g.callers_of(method["usr"], req.relation_types,
                                       include_inferred=req.include_inferred):
                usr = caller["usr"]
                if usr not in seen_caller_usrs:
                    seen_caller_usrs.add(usr)
                    caller["via_method"] = method["name"]
                    caller["depth"] = 1
                    all_callers.append(caller)

        _, status = g.freshness(req.build_id or "")
        open_gaps = _build_open_gaps(g, all_callers, methods, req.usr)
        return BaseToolResponse(
            data=all_callers,
            freshness=status,
            build_id=req.build_id,
            evidence_sources=["call_graph_derivation"],
            open_gaps=open_gaps,
        )

    # ── single-symbol path (existing behaviour) ────────────────────────
    if req.depth > 1:
        data = g.callers_of_depth(req.usr, req.depth, req.relation_types,
                                  include_inferred=req.include_inferred)
    else:
        data = g.callers_of(req.usr, req.relation_types,
                            include_inferred=req.include_inferred)
        data = [{**d, "depth": 1} for d in data]
    _, status = g.freshness(req.build_id or "")
    sym_name = g.symbol(req.usr)
    open_gaps = _build_open_gaps_single(data, sym_name)
    dynamic_binding_hints = []
    if not data:
        dynamic_binding_hints = _build_notification_hints(g, req.usr, req.build_id or "")
        if dynamic_binding_hints and "Dynamic notification binding exists." not in open_gaps:
            open_gaps.append("Dynamic notification binding exists.")
        dynamic_binding_hints.extend(_build_target_action_hints(g, req.usr))
        if dynamic_binding_hints and "Dynamic UIKit target-action binding exists." not in open_gaps:
            if any(hint.get("kind") == "target_action" for hint in dynamic_binding_hints):
                open_gaps.append("Dynamic UIKit target-action binding exists.")
    return BaseToolResponse(
        data=data,
        freshness=status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation"],
        open_gaps=open_gaps,
        dynamic_binding_hints=dynamic_binding_hints,
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


def _build_target_action_hints(
    g: GraphLookup,
    callback_usr: str,
) -> list[dict]:
    """Build summary-only dynamic target-action hints for caller lookups."""
    bindings = g.target_action_bindings_for_callback(callback_usr)
    if not bindings:
        return []
    return [{
        "kind": "target_action",
        "binding_count": len(bindings),
        "bindings": [
            {
                "name": item["name"],
                "file_path": item["file_path"],
                "line": item["line"],
                "control_event": item.get("control_event"),
                "callback_name": item.get("callback_name"),
            }
            for item in bindings
        ],
    }]


def _build_notification_hints(
    g: GraphLookup,
    callback_usr: str,
    build_id: str = "",
) -> list[dict]:
    """Build summary-only dynamic notification hints for caller lookups."""
    rows = g._conn.execute(
        "MATCH (n:Notification)-[ob:Observes]->(cb:Symbol) "
        "WHERE cb.usr = $callback_usr AND ($build_id = '' OR ob.build_id = $build_id) "
        "OPTIONAL MATCH (poster:Symbol)-[ps:Posts]->(n) "
        "WHERE $build_id = '' OR ps.build_id = $build_id "
        "RETURN n.name, ob.selector, ob.observer_usr, ob.observer_name, ob.observer_file_path, "
        "cb.name, cb.module, poster.usr, poster.name, poster.module, poster.file_path",
        {"callback_usr": callback_usr, "build_id": build_id},
    ).get_all()
    if not rows:
        return []
    grouped: dict[tuple[str, str, str, str, str, str, str], dict] = {}
    for row in rows:
        key = (
            row[0] or "",
            row[1] or "",
            row[2] or "",
            row[3] or "",
            row[4] or "",
            row[5] or "",
            row[6] or "",
        )
        entry = grouped.setdefault(
            key,
            {
                "notification_name": row[0],
                "selector": row[1] or "",
                "observer_usr": row[2] or "",
                "name": row[3] or "",
                "file_path": row[4] or "",
                "callback_name": row[5] or "",
                "module": row[6] or "",
                "posters": [],
            },
        )
        if row[7]:
            posters = entry["posters"]
            if not any(p["usr"] == row[7] for p in posters):
                posters.append({
                    "usr": row[7],
                    "name": row[8] or "",
                    "module": row[9] or "",
                    "file_path": row[10] or "",
                })
    return [{
        "kind": "notification",
        "binding_count": len(grouped),
        "bindings": list(grouped.values()),
    }]
