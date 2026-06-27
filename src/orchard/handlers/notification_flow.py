"""MCP handler for notification flow and ObjC semantics.

Returns the semantic role and notification dispatch context for an ObjC
symbol, providing human-readable interpretation of low-level message sends.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.derive.objc_semantics import classify_objc_message
from orchard.derive.notification_graph import build_notification_graph


@dataclass
class NotificationFlowRequest(BaseToolRequest):
    """Request for ObjC notification-flow / semantic analysis of a symbol."""

    usr: str = ""


def get_notification_flow(conn, req: NotificationFlowRequest) -> BaseToolResponse:
    """Analyse the notification-flow / semantic role of a symbol.

    For ObjC symbols this returns the selector's semantic role
    (notification_observer, notification_poster, delegate_setter, ...)
    and a dispatch hint.  For non-ObjC symbols it returns a gap note.
    """
    sym_id = req.usr

    rows = conn.execute(
        "MATCH (s:Symbol {id: $id}) "
        "RETURN s.name, s.language, s.kind, s.module LIMIT 1",
        {"id": sym_id},
    ).get_all()

    if not rows:
        return BaseToolResponse(
            data=None,
            freshness="stale",
            build_id=req.build_id,
            evidence_sources=[],
            open_gaps=[f"symbol '{req.usr}' not found"],
        )

    name, language, kind, module = rows[0]

    if language != "objc":
        return BaseToolResponse(
            data={
                "symbol": name,
                "language": language,
                "semantic_role": "not_applicable",
                "note": "ObjC semantic analysis only applies to ObjC symbols",
            },
            freshness="stale",
            build_id=req.build_id,
            evidence_sources=[],
            open_gaps=["no ObjC semantic analysis available for non-ObjC symbols"],
        )

    # Classify the selector by its own name.
    own_role = classify_objc_message(name)

    # Check notification context — may override role if symbol calls
    # NSNotificationCenter methods.
    notif_graph = build_notification_graph(conn)

    is_observer = any(o["usr"] == req.usr for o in notif_graph["observers"])
    is_publisher = any(p["usr"] == req.usr for p in notif_graph["publishers"])

    # Determine best semantic role: graph-verified roles beat name-only.
    role = own_role
    if is_observer and own_role == "unknown":
        role = "notification_observer"
    elif is_publisher and own_role == "unknown":
        role = "notification_poster"

    dispatch_hint = _build_dispatch_hint(role, name)

    evidence = ["objc_selector_classification"]
    if is_observer or is_publisher:
        evidence.append("notification_graph_detection")

    return BaseToolResponse(
        data={
            "symbol": name,
            "language": language,
            "kind": kind,
            "module": module,
            "semantic_role": role,
            "dispatch_hint": dispatch_hint,
            "is_notification_observer": is_observer,
            "is_notification_poster": is_publisher,
            "notification_peers": {
                "publishers": [
                    p["name"] for p in notif_graph["publishers"]
                    if p["usr"] != req.usr
                ],
                "observers": [
                    o["name"] for o in notif_graph["observers"]
                    if o["usr"] != req.usr
                ],
            },
            "confidence": "inferred",
            "inference_basis": "NSNotificationCenter selector pattern matching",
        },
        freshness="stale",
        build_id=req.build_id,
        evidence_sources=evidence,
        open_gaps=[],
    )


def _build_dispatch_hint(role: str, selector: str) -> str:
    """Build a human-readable dispatch hint for a given semantic role."""
    hints = {
        "notification_observer": (
            f"'{selector}' registers as a notification observer — "
            "likely called by NSNotificationCenter dispatch when the "
            "matching notification is posted"
        ),
        "notification_poster": (
            f"'{selector}' posts a notification — "
            "any registered observers for this notification name "
            "will be dispatched by NSNotificationCenter"
        ),
        "target_action": (
            f"'{selector}' uses target-action pattern — "
            "typically invoked by UIControl event dispatch"
        ),
        "action_sender": (
            f"'{selector}' sends an action — "
            "part of the UIResponder action dispatch chain"
        ),
        "delegate_setter": (
            f"'{selector}' sets a delegate — "
            "the delegate will receive callbacks from the owning object"
        ),
        "data_source": (
            f"'{selector}' sets a data source — "
            "the data source will be queried for content by the owning object"
        ),
        "framework_callback": (
            f"'{selector}' is an Apple framework callback — "
            "called by UIKit/AppKit lifecycle or data-source dispatch"
        ),
        "unknown": (
            f"'{selector}' has no recognised semantic pattern — "
            "may be a custom method or internal dispatch"
        ),
    }
    return hints.get(role, hints["unknown"])
