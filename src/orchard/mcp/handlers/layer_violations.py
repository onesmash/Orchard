"""find_layer_violations — detect Calls crossing heuristic layer boundaries.

Detects two violation patterns:
  1. UI -> Data:  a symbol in a UI-layer module calls a symbol in a Data-layer module.
  2. Data -> Service:  a symbol in a Data-layer module calls a symbol in a Service-layer module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for

# Heuristic layer classification patterns (case-insensitive substring match).
_UI_PATTERNS = ("ui", "view", "screen", "widget", "component", "panel")
_DATA_PATTERNS = ("data", "repository", "store", "database", "db", "persistence", "storage")
_SERVICE_PATTERNS = ("service", "api", "network", "manager", "logic", "business", "interactor")

# Violation patterns: (caller_layer, callee_layer, label).
_VIOLATION_PATTERNS: list[tuple[str, str, str]] = [
    ("ui", "data", "UI→Data"),
    ("data", "service", "Data→Service"),
]


def _classify_module(name: str) -> str:
    """Return the heuristic layer label for a module name.

    Returns one of ``"ui"``, ``"data"``, ``"service"``, or ``"unknown"``.
    """
    lower = name.lower()
    for p in _UI_PATTERNS:
        if p in lower:
            return "ui"
    for p in _DATA_PATTERNS:
        if p in lower:
            return "data"
    for p in _SERVICE_PATTERNS:
        if p in lower:
            return "service"
    return "unknown"


@dataclass
class LayerViolationRequest(BaseToolRequest):
    """Request for detecting layer boundary violations.

    Attributes:
        target_id: The build target identifier.
        include_details: When True (default), each violation includes the
            specific caller/callee symbol USRs and names.
    """

    target_id: str | None = None
    include_details: bool = True


def find_layer_violations(conn, req: LayerViolationRequest) -> BaseToolResponse:
    """Find Calls edges that violate heuristic layer boundaries.

    Scans all Calls edges for the given target, classifies each caller and
    callee module into a layer (ui, data, service, or unknown), then reports
    matches against the predefined violation patterns (UI→Data and Data→Service).

    Parameters
    ----------
    conn
        An open Ladybug connection.
    req
        The layer violation request.

    Returns
    -------
    BaseToolResponse
        ``data = {"violations": [...], "total": N, "by_pattern": {...}}``
    """
    target_id = req.target_id or ""

    # Fetch all Calls edges with module info.
    if target_id:
        rows = conn.execute(
            "MATCH (caller:Symbol {target_id: $tid})-[r:Calls]->(callee:Symbol {target_id: $tid}) "
            "WHERE caller.module IS NOT NULL AND callee.module IS NOT NULL "
            "RETURN caller.usr, caller.name, caller.module, "
            "callee.usr, callee.name, callee.module",
            {"tid": target_id},
        ).get_all()
    else:
        rows = conn.execute(
            "MATCH (caller:Symbol)-[r:Calls]->(callee:Symbol) "
            "WHERE caller.module IS NOT NULL AND callee.module IS NOT NULL "
            "RETURN caller.usr, caller.name, caller.module, "
            "callee.usr, callee.name, callee.module"
        ).get_all()

    # Classify and detect violations.
    violations: list[dict] = []
    pattern_counts: dict[str, int] = {}

    for row in rows:
        caller_usr, caller_name, caller_mod = row[0], row[1], row[2]
        callee_usr, callee_name, callee_mod = row[3], row[4], row[5]

        caller_layer = _classify_module(caller_mod)
        callee_layer = _classify_module(callee_mod)

        for v_caller_layer, v_callee_layer, label in _VIOLATION_PATTERNS:
            if caller_layer == v_caller_layer and callee_layer == v_callee_layer:
                pattern_counts[label] = pattern_counts.get(label, 0) + 1
                entry: dict = {
                    "pattern": label,
                    "caller_module": caller_mod,
                    "callee_module": callee_mod,
                }
                if req.include_details:
                    entry["caller_usr"] = caller_usr
                    entry["caller_name"] = caller_name
                    entry["callee_usr"] = callee_usr
                    entry["callee_name"] = callee_name
                violations.append(entry)
                break  # Each call maps to at most one violation pattern.

    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    return BaseToolResponse(
        data={
            "violations": violations,
            "total": len(violations),
            "by_pattern": pattern_counts,
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["call_graph_derivation", "architecture_derivation"],
        open_gaps=[] if violations else ["no layer violations found"],
    )
