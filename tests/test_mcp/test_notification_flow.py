"""Tests for ObjC message semantics and notification flow.

AC-N1: classify_objc_message correctly categorises ObjC selectors
AC-N2: build_notification_graph finds pub-sub pairs via callee names
AC-N3: get_notification_flow returns dispatch chain for a symbol
"""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.derive.objc_semantics import classify_objc_message
from orchard.derive.notification_graph import build_notification_graph
from orchard.handlers.notification_flow import (
    NotificationFlowRequest, get_notification_flow,
)


# ── AC-N1: selector classification (pure function) ──────────────────

@pytest.mark.parametrize("selector,expected", [
    ("addObserver:selector:name:object:", "notification_observer"),
    ("postNotificationName:object:", "notification_poster"),
    ("postNotificationName:object:userInfo:", "notification_poster"),
    ("addTarget:action:forControlEvents:", "target_action"),
    ("setDelegate:", "delegate_setter"),
    ("setDataSource:", "data_source"),
    ("sendAction:to:from:forEvent:", "action_sender"),
    ("tableView:numberOfRowsInSection:", "framework_callback"),
    ("viewDidLoad", "framework_callback"),
    ("application:didFinishLaunchingWithOptions:", "framework_callback"),
    ("numberOfSectionsInTableView:", "framework_callback"),
    ("someRandomMethod:", "unknown"),
    ("plainMethod", "unknown"),
])
def test_classify_objc_message(selector, expected):
    assert classify_objc_message(selector) == expected


# ── AC-N2: notification graph ───────────────────────────────────────

@pytest.fixture
def conn_with_notifications(tmp_db_path):
    """DB with NSNotificationCenter post/observe call patterns.

    Models calls via the callee symbol name as the ObjC selector.
    """
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # callee symbols whose names ARE ObjC selectors
    callees = [
        ("s:addObserver", "addObserver:selector:name:object:", "objc.method"),
        ("s:postNotification", "postNotificationName:object:", "objc.method"),
    ]
    callers = [
        ("s:PublisherA", "postSomeEvent", "objc.method"),
        ("s:ObserverB", "handleSomeEvent:", "objc.method"),
        ("s:ObserverC", "handleSomeEventToo:", "objc.method"),
        ("s:Unrelated", "doSomething", "objc.method"),
    ]
    for sym_id, name, kind in callees + callers:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: '{sym_id}', precise_id: '', "
            f"name: '{name}', language: 'objc', kind: '{kind}', module: 'Zoom', "
            f"target_id: 'T1', file_path: '/src/app.mm', signature: '', "
            f"container_usr: '', access_level: 'internal', origin: 'derived', "
            f"is_generated: false}})"
        )
    # PublisherA calls postNotificationName:object:
    conn.execute(
        "MATCH (a:Symbol {id:'s:PublisherA'}), (nc:Symbol {id:'s:postNotification'}) "
        "CREATE (a)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(nc)"
    )
    # ObserverB calls addObserver:selector:name:object:
    conn.execute(
        "MATCH (b:Symbol {id:'s:ObserverB'}), (nc:Symbol {id:'s:addObserver'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(nc)"
    )
    # ObserverC also calls addObserver:...
    conn.execute(
        "MATCH (c:Symbol {id:'s:ObserverC'}), (nc:Symbol {id:'s:addObserver'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b1', reason:'source_direct'}]->(nc)"
    )
    yield conn
    conn.close()


def test_build_notification_graph_finds_publishers_and_observers(conn_with_notifications):
    """AC-N2: Notification graph detects post/observe patterns via callee names."""
    graph = build_notification_graph(conn_with_notifications)

    assert "publishers" in graph
    assert "observers" in graph
    assert len(graph["publishers"]) >= 1
    assert len(graph["observers"]) >= 2

    publisher_names = {p["name"] for p in graph["publishers"]}
    assert "postSomeEvent" in publisher_names

    observer_names = {o["name"] for o in graph["observers"]}
    assert "handleSomeEvent:" in observer_names
    assert "handleSomeEventToo:" in observer_names


def test_build_notification_graph_empty_when_no_notifications(tmp_db_path):
    """Empty graph when no NSNotificationCenter selectors present."""
    conn2 = get_connection(tmp_db_path)
    init_schema(conn2)
    conn2.execute(
        "CREATE (:Symbol {id: 's:A', usr: 's:A', precise_id: '', "
        "name: 'A', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/a.swift', signature: '', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    graph = build_notification_graph(conn2)
    assert graph["publishers"] == []
    assert graph["observers"] == []
    conn2.close()


# ── AC-N3: notification flow for a specific symbol ──────────────────

def test_get_notification_flow_returns_chain(conn_with_notifications):
    """AC-N3: get_notification_flow returns dispatch chain for observer."""
    req = NotificationFlowRequest(usr="s:ObserverB")
    resp = get_notification_flow(conn_with_notifications, req)

    assert resp.data is not None
    assert resp.data["symbol"] == "handleSomeEvent:"
    assert resp.data["semantic_role"] == "notification_observer"
    assert "dispatch_hint" in resp.data
    assert resp.data["confidence"] == "inferred"
    assert "NSNotificationCenter" in resp.data["inference_basis"]


def test_get_notification_flow_non_objc(conn_with_notifications):
    """Non-ObjC symbol returns not_applicable role."""
    conn_with_notifications.execute(
        "CREATE (:Symbol {id: 's:swiftFunc', usr: 's:swiftFunc', precise_id: '', "
        "name: 'swiftFunc', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/app.swift', signature: '', "
        "container_usr: '', access_level: 'internal', origin: 'derived', "
        "is_generated: false})"
    )
    req = NotificationFlowRequest(usr="s:swiftFunc")
    resp = get_notification_flow(conn_with_notifications, req)
    assert "no ObjC semantic analysis" in str(resp.open_gaps).lower() or resp.data["semantic_role"] == "not_applicable"
