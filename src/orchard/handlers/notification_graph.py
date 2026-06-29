"""notification_graph handler — query Notification/Posts/Observes from the graph.

Provides the MCP entry point for Orchard's notification-graph capability,
matching the CLI ``orchard notification-graph`` command.

Two grouping modes:
  - ``group_by = "notification"`` (default): grouped by notification name
  - ``group_by = "observer"``: grouped by observer USR, showing each observer's
    registrations (selector, notification_name, callback)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.derive.notification_graph import _query_persisted_graph
from orchard.validation.freshness import freshness_for


@dataclass
class NotificationGraphRequest(BaseToolRequest):
    notification_name: str = ""
    group_by: str = "notification"


def get_notification_graph(conn, req: NotificationGraphRequest) -> BaseToolResponse:
    """Return the notification publisher-observer graph.

    Query persisted Notification nodes and Posts/Observes edges.
    Optionally filter by *notification_name* (substring match).
    With *group_by = "observer"*, returns the graph pivoted by observer.
    """
    graph = _query_persisted_graph(conn, req.notification_name)

    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    notifications = graph.get("notifications", {})

    if req.group_by == "observer":
        observers = _pivot_by_observer(notifications)
        return BaseToolResponse(
            data={"observers": observers},
            freshness=freshness_status,
            build_id=req.build_id,
            evidence_sources=["notification_graph_derivation"],
            open_gaps=[] if observers else ["no notification edges found"],
        )

    return BaseToolResponse(
        data={
            "notifications": {
                k: {"posters": v["posters"], "observers": v["observers"]}
                for k, v in notifications.items()
            },
            "target_actions": graph.get("target_actions", []),
        },
        freshness=freshness_status,
        build_id=req.build_id,
        evidence_sources=["notification_graph_derivation"],
        open_gaps=[] if notifications else ["no notification edges found"],
    )


def _pivot_by_observer(notifications: dict) -> dict:
    """Pivot notification→observers into observer→registrations.

    Returns:
        {obs_usr: {name, file_path, registrations: [{notification_name, selector, callback}]}}
    """
    observers: dict[str, dict] = {}
    for noti_name, data in notifications.items():
        for obs in data.get("observers", []):
            usr = obs.get("usr", "")
            if not usr:
                continue
            if usr not in observers:
                observers[usr] = {
                    "name": obs.get("name", ""),
                    "file_path": obs.get("file_path", ""),
                    "registrations": [],
                }
            observers[usr]["registrations"].append({
                "notification_name": noti_name,
                "selector": obs.get("selector", ""),
                "callback": obs.get("callback"),
            })
    return observers
