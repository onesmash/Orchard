"""Tests for orchard_search by-kind grouping and disambiguation UI.

AC-4: orchard_search 结果按 kind 分组，提供结构化符号视图
AC-4.1: _do_search_name 返回 by_kind 字典
AC-4.2: by_kind 每个键下的符号属于对应的 kind
AC-4.3: results 扁平列表仍然存在（向后兼容）
"""
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


# ── AC-4.1: by_kind grouping exists ──────────────────────────────
def test_search_name_returns_by_kind_grouping(conn_with_mixed_symbols):
    """AC-4.1: Response includes by_kind dict grouping results by kind."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "toolbarItems"})
        result = json.loads(result_json)

        assert "by_kind" in result, "Response must include by_kind grouping"
        assert isinstance(result["by_kind"], dict)
        assert len(result["by_kind"]) >= 3, (
            f"Expected at least 3 kinds for 'toolbarItems', got {len(result['by_kind'])}"
        )
    finally:
        server_mod._conn = original_conn


# ── AC-4.2: correct kind assignment ───────────────────────────────
def test_search_name_by_kind_correct_assignment(conn_with_mixed_symbols):
    """AC-4.2: Each kind key contains only symbols of that kind."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "toolbarItems"})
        result = json.loads(result_json)

        for kind, symbols in result["by_kind"].items():
            for sym in symbols:
                assert sym["kind"] == kind, (
                    f"Symbol {sym['name']} has kind={sym['kind']} but is in group {kind}"
                )
    finally:
        server_mod._conn = original_conn


# ── AC-4.3: backward-compatible results list ─────────────────────
def test_search_name_retains_flat_results(conn_with_mixed_symbols):
    """AC-4.3: The flat results list is still present for backward compat."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "toolbarItems"})
        result = json.loads(result_json)

        assert "results" in result, "Backward-compatible 'results' list must exist"
        assert isinstance(result["results"], list)
        assert result["count"] == len(result["results"])

        # Total in by_kind should match count
        total_in_groups = sum(len(v) for v in result["by_kind"].values())
        assert total_in_groups == result["count"], (
            f"by_kind total {total_in_groups} != count {result['count']}"
        )
    finally:
        server_mod._conn = original_conn


# ── edge: single-kind results ────────────────────────────────────
def test_search_name_single_kind(conn_with_mixed_symbols):
    """When only one kind matches, by_kind has a single entry."""
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = conn_with_mixed_symbols

    try:
        result_json = server_mod._do_search_name({"name": "firstSectionItems"})
        result = json.loads(result_json)

        assert len(result["by_kind"]) == 1
        assert "objc.method" in result["by_kind"]
        assert len(result["results"]) == 1
    finally:
        server_mod._conn = original_conn
