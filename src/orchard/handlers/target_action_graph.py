"""target_action_graph handler — query UIKit target-action bindings."""

from __future__ import annotations

from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.derive.notification_graph import build_notification_graph
from orchard.validation.freshness import freshness_for


@dataclass
class TargetActionGraphRequest(BaseToolRequest):
    selector: str = ""
    callback_usr: str = ""
    file: str = ""
    group_by: str = "callback"


def get_target_action_graph(conn, req: TargetActionGraphRequest) -> BaseToolResponse:
    """Return target-action bindings grouped by callback or registrar."""
    source_root = req.repo_root or _workspace_root(conn)
    graph = build_notification_graph(conn, source_root=source_root)
    bindings = _filter_bindings(graph.get("target_actions", []), req)

    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    if req.group_by == "registrar":
        registrars = _group_by_registrar(bindings)
        return BaseToolResponse(
            data={"registrars": registrars},
            freshness=freshness_status,
            build_id=req.build_id,
            evidence_sources=["target_action_derivation"],
            open_gaps=[] if registrars else ["no target-action bindings found"],
        )

    callbacks = _group_by_callback(bindings)
    return BaseToolResponse(
        data={"callbacks": callbacks},
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["target_action_derivation"],
        open_gaps=[] if callbacks else ["no target-action bindings found"],
    )


def _workspace_root(conn) -> str:
    rows = conn.execute(
        "MATCH (b:BuildSnapshot) "
        "RETURN b.workspace_root ORDER BY b.created_at DESC LIMIT 1"
    ).get_all()
    return rows[0][0] if rows and rows[0][0] else ""


def _filter_bindings(bindings: list[dict], req: TargetActionGraphRequest) -> list[dict]:
    results: list[dict] = []
    for entry in bindings:
        callback = entry.get("callback") or {}
        if req.selector and req.selector != entry.get("selector"):
            continue
        if req.callback_usr and req.callback_usr != callback.get("usr"):
            continue
        if req.file and req.file not in (entry.get("file_path") or ""):
            continue
        results.append(entry)
    return results


def _group_by_callback(bindings: list[dict]) -> dict:
    grouped: dict[str, dict] = {}
    for entry in bindings:
        callback = entry.get("callback")
        if not callback or not callback.get("usr"):
            continue
        key = callback["usr"]
        if key not in grouped:
            grouped[key] = {
                "callback": {
                    "usr": callback.get("usr"),
                    "name": callback.get("name"),
                    "module": callback.get("module") or "",
                },
                "bindings": [],
            }
        grouped[key]["bindings"].append(_binding_payload(entry))
    return grouped


def _group_by_registrar(bindings: list[dict]) -> dict:
    grouped: dict[str, dict] = {}
    for entry in bindings:
        key = entry.get("usr") or ""
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {
                "registrar": {
                    "usr": entry.get("usr"),
                    "name": entry.get("name"),
                    "module": entry.get("module") or "",
                    "file_path": entry.get("file_path") or "",
                },
                "bindings": [],
            }
        grouped[key]["bindings"].append(_binding_payload(entry))
    return grouped


def _binding_payload(entry: dict) -> dict:
    return {
        "usr": entry.get("usr"),
        "name": entry.get("name"),
        "file_path": entry.get("file_path"),
        "module": entry.get("module") or "",
        "line": entry.get("line"),
        "selector": entry.get("selector"),
        "control_event": entry.get("control_event"),
    }
