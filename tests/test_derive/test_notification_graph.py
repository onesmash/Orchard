"""Tests for notification publisher-observer graph with @selector extraction."""

import pytest
import os
from orchard.graph.db import get_connection, init_schema
from orchard.derive.notification_graph import (
    parse_addobserver_line,
    build_notification_graph,
    persist_notification_graph,
)


# ── RED: parse_addobserver_line ──────────────────────────────────────

class TestParseAddObserverLine:
    def test_extracts_selector_and_notification_name(self):
        line = '[[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(viewDidTransitionToSize:) name:kNoti_ViewDidTransitionToSize object:nil];'
        result = parse_addobserver_line(line)
        assert result == ("viewDidTransitionToSize:", "kNoti_ViewDidTransitionToSize")

    def test_extracts_selector_with_multiple_params(self):
        line = '[nc addObserver:self selector:@selector(handleNotification:) name:@"SomeNotification" object:nil];'
        result = parse_addobserver_line(line)
        assert result == ("handleNotification:", '@"SomeNotification"')

    def test_returns_none_for_non_addobserver_line(self):
        assert parse_addobserver_line("int x = 5;") is None
        assert parse_addobserver_line('[self setupUI];') is None

    def test_extracts_block_based_observer(self):
        line = '[nc addObserverForName:@"MyNotification" object:nil queue:nil usingBlock:^(NSNotification *note) { }];'
        result = parse_addobserver_line(line)
        assert result is not None
        assert result[0] is None  # no @selector
        assert result[1] == '@"MyNotification"'


class TestParsePostNotificationLine:
    def test_extracts_notification_name(self):
        from orchard.derive.notification_graph import parse_post_notification_line
        line = '[[NSNotificationCenter defaultCenter] postNotificationName:@"MyNotification" object:nil];'
        result = parse_post_notification_line(line)
        assert result == '@"MyNotification"'

    def test_extracts_const_notification(self):
        from orchard.derive.notification_graph import parse_post_notification_line
        line = '[nc postNotificationName:kSomeNotification object:self];'
        result = parse_post_notification_line(line)
        assert result == "kSomeNotification"


# ── RED: build_notification_graph with source files ──────────────────

@pytest.fixture
def conn_with_notifications(tmp_path):
    """DB + source files for notification graph testing."""
    db_path = str(tmp_path / "graph.db")
    conn = get_connection(db_path)
    init_schema(conn)

    # Source files on disk
    observer_file = tmp_path / "Observer.m"
    observer_file.write_text("""\
@implementation MyObserver
- (void)setupNotifications {
    [[NSNotificationCenter defaultCenter] addObserver:self
        selector:@selector(handleSomething:)
        name:kSomethingHappened object:nil];
}
- (void)handleSomething:(NSNotification *)note {
    // callback
}
@end
""")
    poster_file = tmp_path / "Poster.m"
    poster_file.write_text("""\
@implementation MyPoster
- (void)trigger {
    [[NSNotificationCenter defaultCenter]
        postNotificationName:kSomethingHappened object:self];
}
@end
""")

    # Symbols
    for sym_data in [
        ("s:observer_setup", "setupNotifications", "objc", "method",
         str(observer_file), "MyModule"),
        ("s:observer_callback", "handleSomething:", "objc", "method",
         str(observer_file), "MyModule"),
        ("s:poster_trigger", "trigger", "objc", "method",
         str(poster_file), "MyModule"),
        ("s:nsNotifyCtr", "NSNotificationCenter", "objc", "class",
         "/System/Library/Frameworks/Foundation.framework/Headers/NSNotificationCenter.h",
         "Foundation"),
        ("s:addObserver", "addObserver:selector:name:object:", "objc", "method",
         "/System/Library/Frameworks/Foundation.framework/Headers/NSNotificationCenter.h",
         "Foundation"),
        ("s:postNotification", "postNotificationName:object:", "objc", "method",
         "/System/Library/Frameworks/Foundation.framework/Headers/NSNotificationCenter.h",
         "Foundation"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_data[0]}', usr: '{sym_data[0]}', "
            f"precise_id: '', name: '{sym_data[1]}', language: '{sym_data[2]}', "
            f"kind: '{sym_data[3]}', module: '{sym_data[5]}', "
            f"file_path: '{sym_data[4]}', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )

    # File nodes
    for path in [str(observer_file), str(poster_file)]:
        conn.execute(
            f"CREATE (:File {{path: '{path}', module: 'MyModule', "
            f"language: 'objc', is_generated: false}})"
        )

    # Calls: setupNotifications → addObserver:selector:name:object:
    conn.execute(
        "MATCH (c:Symbol {id:'s:observer_setup'}), "
        "(t:Symbol {id:'s:addObserver'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, "
        "provenance:'indexstore', build_id:'b1', "
        "reason:'source_direct'}]->(t)"
    )
    # Calls: trigger → postNotificationName:object:
    conn.execute(
        "MATCH (c:Symbol {id:'s:poster_trigger'}), "
        "(t:Symbol {id:'s:postNotification'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, "
        "provenance:'indexstore', build_id:'b1', "
        "reason:'source_direct'}]->(t)"
    )

    yield conn, tmp_path
    conn.close()


def test_build_notification_graph_links_observer_to_callback(conn_with_notifications):
    """Observer registration is linked to @selector callback and notification name."""
    conn, tmp_path = conn_with_notifications
    graph = build_notification_graph(conn, source_root=str(tmp_path))

    assert "notifications" in graph
    noti = graph["notifications"].get("kSomethingHappened")
    assert noti is not None, f"Expected kSomethingHappened in notifications, got {list(graph['notifications'].keys())}"

    # Check posters
    assert len(noti["posters"]) == 1
    assert noti["posters"][0]["name"] == "trigger"
    assert noti["posters"][0]["line"] > 0

    # Check observers
    assert len(noti["observers"]) == 1
    obs = noti["observers"][0]
    assert obs["name"] == "setupNotifications"
    assert obs["selector"] == "handleSomething:"
    assert obs["notification_name"] == "kSomethingHappened"
    assert obs["line"] > 0
    assert obs["callback"]["name"] == "handleSomething:"
    assert "s:observer_callback" in obs["callback"]["usr"]


def test_build_notification_graph_scans_only_notification_caller_files(conn_with_notifications, monkeypatch):
    conn, tmp_path = conn_with_notifications
    captured: dict[str, object] = {}

    def fake_grep_files(root: str, pattern: str, window: int = 5, file_list=None):
        captured["file_list"] = file_list
        return {}

    monkeypatch.setattr("orchard.derive.notification_graph._grep_files", fake_grep_files)

    build_notification_graph(
        conn,
        source_root=str(tmp_path),
        changed_files=[
            str(tmp_path / "Observer.m"),
            str(tmp_path / "Poster.m"),
            str(tmp_path / "Unrelated.swift"),
        ],
    )

    assert set(captured["file_list"]) == {
        str(tmp_path / "Observer.m"),
        str(tmp_path / "Poster.m"),
    }


def test_build_notification_graph_empty_without_observers(tmp_path):
    """No notification edges → empty graph."""
    db_path = str(tmp_path / "empty.db")
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 's:x', usr: 's:x', precise_id: '', "
        "name: 'someFunc', language: 'swift', kind: 'swift.func', "
        "module: 'M', file_path: '/f.swift', "
        "signature: '', container_usr: '', access_level: 'internal', "
        "origin: 'derived', is_generated: false})"
    )
    graph = build_notification_graph(conn)
    assert graph["notifications"] == {}
    assert graph["publishers"] == []
    assert graph["observers"] == []
    assert graph["target_actions"] == []


# ── RED: persist_notification_graph ───────────────────────────────────

def test_persist_creates_notification_nodes_and_edges(conn_with_notifications):
    """Posts and Observes edges link posters to callbacks via Notification nodes."""
    conn, tmp_path = conn_with_notifications
    count = persist_notification_graph(conn, source_root=str(tmp_path),
                                       build_id="b1")

    assert count > 0  # at least one Notifies pair written

    # Verify Notification node created
    n_rows = conn.execute(
        "MATCH (n:Notification {name: $name}) RETURN n.name",
        {"name": "kSomethingHappened"},
    ).get_all()
    assert len(n_rows) == 1

    # Verify Posts edge: trigger → Notification
    p_rows = conn.execute(
        "MATCH (s:Symbol {name: 'trigger'})-[r:Posts]->(n:Notification {name: 'kSomethingHappened'}) "
        "RETURN r.confidence"
    ).get_all()
    assert len(p_rows) == 1
    assert float(p_rows[0][0]) == 0.70

    # Verify Observes edge: Notification → callback
    o_rows = conn.execute(
        "MATCH (n:Notification {name: 'kSomethingHappened'})-[r:Observes]->"
        "(cb:Symbol {name: 'handleSomething:'}) "
        "RETURN r.selector"
    ).get_all()
    assert len(o_rows) == 1
    assert o_rows[0][0] == "handleSomething:"


def test_persist_is_idempotent(conn_with_notifications):
    """Second call with same data should not create duplicates."""
    conn, tmp_path = conn_with_notifications
    c1 = persist_notification_graph(conn, source_root=str(tmp_path),
                                    build_id="b1")
    c2 = persist_notification_graph(conn, source_root=str(tmp_path),
                                    build_id="b2")

    assert c1 == c2  # same number of edges written
    # Verify no duplicate Notification nodes
    n_rows = conn.execute(
        "MATCH (n:Notification {name: 'kSomethingHappened'}) RETURN count(n)"
    ).get_all()
    assert n_rows[0][0] == 1  # still only one


def test_build_notification_graph_batches_callback_symbol_lookup(conn_with_notifications):
    conn, tmp_path = conn_with_notifications

    second_file = tmp_path / "ObserverTwo.m"
    second_file.write_text("""\
@implementation MyObserverTwo
- (void)setupMoreNotifications {
    [[NSNotificationCenter defaultCenter] addObserver:self
        selector:@selector(handleOtherThing:)
        name:kSomethingHappened object:nil];
}
- (void)handleOtherThing:(NSNotification *)note {
}
@end
""")

    for sym_data in [
        ("s:observer_setup_two", "setupMoreNotifications", "objc", "method",
         str(second_file), "MyModule"),
        ("s:observer_callback_two", "handleOtherThing:", "objc", "method",
         str(second_file), "MyModule"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_data[0]}', usr: '{sym_data[0]}', "
            f"precise_id: '', name: '{sym_data[1]}', language: '{sym_data[2]}', "
            f"kind: '{sym_data[3]}', module: '{sym_data[5]}', "
            f"file_path: '{sym_data[4]}', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )
    conn.execute(
        "MATCH (c:Symbol {id:'s:observer_setup_two'}), "
        "(t:Symbol {id:'s:addObserver'}) "
        "CREATE (c)-[:Calls {source:'derived', confidence:1.0, "
        "provenance:'indexstore', build_id:'b1', "
        "reason:'source_direct'}]->(t)"
    )

    callback_queries = {"count": 0}
    original_execute = conn.execute

    def counting_execute(query, params=None):
        if "RETURN s.file_path, s.name, s.usr, s.kind, s.module" in query:
            callback_queries["count"] += 1
        return original_execute(query, params)

    conn.execute = counting_execute
    try:
        graph = build_notification_graph(conn, source_root=str(tmp_path))
    finally:
        conn.execute = original_execute

    observers = graph["notifications"]["kSomethingHappened"]["observers"]
    callbacks = {obs["callback"]["name"] for obs in observers if obs["callback"]}
    assert callbacks == {"handleSomething:", "handleOtherThing:"}
    assert callback_queries["count"] == 1
