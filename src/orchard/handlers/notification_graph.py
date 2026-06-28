"""notification_graph handler — query Notification/Posts/Observes from the graph.

Provides the MCP entry point for Orchard's notification-graph capability,
matching the CLI ``orchard notification-graph`` command.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchard.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.derive.notification_graph import _query_persisted_graph
from orchard.validation.freshness import freshness_for


@dataclass
class NotificationGraphRequest(BaseToolRequest):
    notification_name: str = ""


def get_notification_graph(conn, req: NotificationGraphRequest) -> BaseToolResponse:
    """Return the notification publisher-observer graph.

    Query persisted Notification nodes and Posts/Observes edges.
    Optionally filter by *notification_name* (substring match).
    """
    graph = _query_persisted_graph(conn, req.notification_name)

    _, freshness_status = freshness_for(conn, req.build_id or "", {})

    notifications = graph.get("notifications", {})
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
