"""Tests for notification publisher-observer graph with @selector extraction."""

import pytest
import os
from orchard.graph.db import get_connection, init_schema
from orchard.derive.notification_graph import (
    parse_addobserver_line,
    build_notification_graph,
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
            f"kind: '{sym_data[3]}', module: '{sym_data[5]}', target_id: 'T', "
            f"file_path: '{sym_data[4]}', signature: '', container_usr: '', "
            f"access_level: 'internal', origin: 'derived', is_generated: false}})"
        )

    # File nodes
    for path in [str(observer_file), str(poster_file)]:
        conn.execute(
            f"CREATE (:File {{path: '{path}', module: 'MyModule', "
            f"language: 'objc', target_id: 'T', is_generated: false}})"
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


def test_build_notification_graph_empty_without_observers(tmp_path):
    """No notification edges → empty graph."""
    db_path = str(tmp_path / "empty.db")
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 's:x', usr: 's:x', precise_id: '', "
        "name: 'someFunc', language: 'swift', kind: 'swift.func', "
        "module: 'M', target_id: 'T', file_path: '/f.swift', "
        "signature: '', container_usr: '', access_level: 'internal', "
        "origin: 'derived', is_generated: false})"
    )
    graph = build_notification_graph(conn)
    assert graph["notifications"] == {}
    assert graph["publishers"] == []
    assert graph["observers"] == []
    assert graph["target_actions"] == []
    conn.close()
