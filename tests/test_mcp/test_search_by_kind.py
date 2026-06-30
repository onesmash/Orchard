"""Tests for guided orchard_search response shape and ranking."""
import pytest
import json
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn_with_mixed_symbols(tmp_db_path):
    """Database with symbols of different kinds (field, method, property, class)."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    symbols = [
        ("s:ToolbarItems", "toolbarItems", "objc.field"),
        ("s:ToolbarItems_method", "toolbarItems", "objc.method"),
        ("s:ToolbarItems_prop", "toolbarItems", "objc.property"),
        ("s:Business", "ZMMeetingToolbarBusiness", "objc.class"),
        ("s:UpdateToolbar", "updateToolbarItems", "objc.method"),
        ("s:FirstSection", "firstSectionItems", "objc.method"),
    ]
    for sym_id, name, kind in symbols:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: '{sym_id}', precise_id: '', "
            f"name: '{name}', language: 'objc', kind: '{kind}', module: 'Zoom', "
            f"file_path: '/src/meeting.mm', signature: '', "
            f"container_usr: '', access_level: 'internal', origin: 'derived', "
            f"is_generated: false}})"
        )
    yield conn
    conn.close()


def test_search_name_returns_compact_status_and_next(conn_with_mixed_symbols):
    """Search response includes query/status/matches/next in compact shape."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "toolbarItems"})
        result = json.loads(result_json)

        assert result["query"]["kind"] == "symbol"
        assert result["status"]["outcome"] == "ambiguous"
        assert result["status"]["coverage"] == "covered"
        assert "freshness" in result["status"]
        assert "matches" in result
        assert "next" in result
    finally:
        server_mod._conn = original_conn


def test_search_name_matches_are_sorted_deterministically(conn_with_mixed_symbols):
    """Ambiguous exact-name matches are returned in deterministic order."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "toolbarItems"})
        result = json.loads(result_json)

        assert [sym["usr"] for sym in result["matches"]] == [
            "s:ToolbarItems",
            "s:ToolbarItems_method",
            "s:ToolbarItems_prop",
        ]
    finally:
        server_mod._conn = original_conn


def test_search_name_no_match_prefers_shell_text_search_after_owner_hint(conn_with_mixed_symbols):
    """No-match responses keep executable fallback actions."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "process_msg"})
        result = json.loads(result_json)

        assert result["status"]["outcome"] == "no_match"
        assert result["next"][-1]["tool"] == "shell_text_search"
    finally:
        server_mod._conn = original_conn


def test_search_name_frame_like_input_routes_to_lookup_frame(conn_with_mixed_symbols):
    """Frame-like input should route to the dedicated frame lookup tool."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name(
            {"name": "ssb::thread_wrapper_t::process_msg(unsigned int)"}
        )
        result = json.loads(result_json)

        assert "frame_lookup_recommended" in result["diag"]
        assert result["next"][0]["tool"] == "orchard_lookup_frame"
    finally:
        server_mod._conn = original_conn
