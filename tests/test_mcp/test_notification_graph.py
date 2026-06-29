"""Tests for orchard_notification_graph MCP handler.

Uses persisted Notification / Posts / Observes data so the handler can
query the graph without touching filesystem grep.

Phase 1+3 adds:
  - observer identity fields (observer_usr, observer_name, observer_file_path)
  - group_by observer mode
"""

import pytest
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn_with_notifications(tmp_db_path):
    """Populated graph with Notification nodes, Posts, and Observes edges.

    Observes edges now carry observer_usr/observer_name/observer_file_path
    so the query can return the full notification_bridge: who registered,
    which selector, which notification, and which callback.
    """
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    # Symbols: two posters (A, B), observer (C), callback (D).
    for sym_id, name, kind, file_path in [
        ("s:A", "postNotificationA()", "objc.method", "/src/PosterA.m"),
        ("s:B", "postNotificationB()", "objc.method", "/src/PosterB.m"),
        ("s:C", "registerNotifications()", "objc.method", "/src/Observer.m"),
        ("s:D", "handleNotification:", "objc.method", "/src/Observer.m"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'objc', kind: '{kind}', module: 'M', "
            f"file_path: '{file_path}', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )

    # Notification nodes.
    conn.execute("CREATE (:Notification {name: 'MyNotification'})")
    conn.execute("CREATE (:Notification {name: 'OtherNotification'})")

    # Posts edges: A → MyNotification, B → OtherNotification.
    conn.execute(
        "MATCH (a:Symbol {id:'s:A'}), (n:Notification {name:'MyNotification'}) "
        "CREATE (a)-[:Posts {confidence:0.7, provenance:'derive/notification', "
        "build_id:'b1'}]->(n)"
    )
    conn.execute(
        "MATCH (b:Symbol {id:'s:B'}), (n:Notification {name:'OtherNotification'}) "
        "CREATE (b)-[:Posts {confidence:0.7, provenance:'derive/notification', "
        "build_id:'b1'}]->(n)"
    )

    # Observes edge: MyNotification → D (callback).  C is the observer.
    conn.execute(
        "MATCH (n:Notification {name:'MyNotification'}), (d:Symbol {id:'s:D'}) "
        "CREATE (n)-[:Observes {selector:'handleNotification:', "
        "observer_usr:'s:registerNotifications()', "
        "observer_name:'registerNotifications()', "
        "observer_file_path:'/src/Observer.m', "
        "confidence:0.7, provenance:'derive/notification', build_id:'b1'}]->(d)"
    )

    yield conn
    conn.close()


# ── AC-1: full graph returns all notifications ────────────────────────
def test_notification_graph_returns_all_notifications(conn_with_notifications):
    """AC-1: All notification names appear in results."""
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    req = NotificationGraphRequest(build_id="b1")
    resp = get_notification_graph(conn_with_notifications, req)

    assert resp.data is not None
    notifications = resp.data.get("notifications", {})
    assert "MyNotification" in notifications
    assert "OtherNotification" in notifications


# ── AC-2: observer identity fields (Phase 1) ─────────────────────────
def test_notification_graph_observer_identity_fields(conn_with_notifications):
    """AC-2: Observer carries usr/name/file_path — the full notification_bridge."""
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    req = NotificationGraphRequest(build_id="b1")
    resp = get_notification_graph(conn_with_notifications, req)

    notifications = resp.data["notifications"]
    my_noti = notifications["MyNotification"]
    assert len(my_noti["posters"]) == 1
    assert my_noti["posters"][0]["name"] == "postNotificationA()"

    assert len(my_noti["observers"]) == 1
    obs = my_noti["observers"][0]
    assert obs["selector"] == "handleNotification:"
    assert obs["notification_name"] == "MyNotification"
    # Phase 1: observer identity
    assert obs["usr"] == "s:registerNotifications()", "observer usr"
    assert obs["name"] == "registerNotifications()", "observer name"
    assert obs["file_path"] == "/src/Observer.m", "observer file_path"
    # Callback
    assert obs["callback"] is not None
    assert obs["callback"]["name"] == "handleNotification:"


# ── AC-3: filter by notification name ─────────────────────────────────
def test_notification_graph_filter_by_name(conn_with_notifications):
    """AC-3: --notification-name filter returns only matching entries."""
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    req = NotificationGraphRequest(
        notification_name="OtherNotification", build_id="b1",
    )
    resp = get_notification_graph(conn_with_notifications, req)

    notifications = resp.data["notifications"]
    assert "OtherNotification" in notifications
    assert "MyNotification" not in notifications
    assert len(notifications["OtherNotification"]["posters"]) == 1
    assert notifications["OtherNotification"]["posters"][0]["name"] == "postNotificationB()"


# ── AC-4: empty graph returns empty results ───────────────────────────
def test_notification_graph_empty(tmp_db_path):
    """AC-4: Empty database returns empty notifications dict."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    req = NotificationGraphRequest(build_id="")
    resp = get_notification_graph(conn, req)
    conn.close()

    assert resp.data is not None
    assert resp.data.get("notifications", {"sentinel": True}) == {}


# ── AC-5: observer-only notification (no poster) is included ──────────
def test_notification_graph_observer_only(conn_with_notifications):
    """AC-5: Notifications with only observers (no Posters edge) still appear."""
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    # Add an observer-only notification with observer identity.
    conn_with_notifications.execute(
        "CREATE (:Notification {name: 'SilentNotification'})"
    )
    conn_with_notifications.execute(
        "CREATE (:Symbol {id: 's:E', usr: 's:silentObserver()', precise_id: '', "
        "name: 'silentObserver()', language: 'objc', kind: 'objc.method', "
        "module: 'M', file_path: '/src/Silent.m', signature: '', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    conn_with_notifications.execute(
        "MATCH (n:Notification {name:'SilentNotification'}), (e:Symbol {id:'s:E'}) "
        "CREATE (n)-[:Observes {selector:'silentObserver:', "
        "observer_usr:'s:silentObserver()', observer_name:'silentObserver()', "
        "observer_file_path:'/src/Silent.m', "
        "confidence:0.7, provenance:'derive/notification', build_id:'b1'}]->(e)"
    )

    req = NotificationGraphRequest(build_id="b1")
    resp = get_notification_graph(conn_with_notifications, req)

    notifications = resp.data["notifications"]
    assert "SilentNotification" in notifications
    silent = notifications["SilentNotification"]
    assert silent["posters"] == []
    assert len(silent["observers"]) == 1
    obs = silent["observers"][0]
    assert obs["usr"] == "s:silentObserver()"
    assert obs["name"] == "silentObserver()"
    assert obs["file_path"] == "/src/Silent.m"


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: group_by observer mode
# ═══════════════════════════════════════════════════════════════════════

def test_notification_graph_group_by_observer(conn_with_notifications):
    """AC-6: group_by='observer' returns observers as top-level keys."""
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    req = NotificationGraphRequest(build_id="b1", group_by="observer")
    resp = get_notification_graph(conn_with_notifications, req)

    observers = resp.data.get("observers", {})
    assert "s:registerNotifications()" in observers
    obs_data = observers["s:registerNotifications()"]
    assert obs_data["name"] == "registerNotifications()"
    assert obs_data["file_path"] == "/src/Observer.m"
    assert len(obs_data["registrations"]) == 1
    reg = obs_data["registrations"][0]
    assert reg["notification_name"] == "MyNotification"
    assert reg["selector"] == "handleNotification:"
    assert reg["callback"]["name"] == "handleNotification:"


def test_notification_graph_group_by_observer_empty(conn_with_notifications):
    """AC-7: group_by='observer' on empty graph returns empty observers dict."""
    from orchard.handlers.notification_graph import (
        NotificationGraphRequest, get_notification_graph,
    )
    # OtherNotification has no observers → should not appear in observer view.
    req = NotificationGraphRequest(
        build_id="b1", group_by="observer",
        notification_name="OtherNotification",
    )
    resp = get_notification_graph(conn_with_notifications, req)
    assert resp.data.get("observers", {"x": 1}) == {}
