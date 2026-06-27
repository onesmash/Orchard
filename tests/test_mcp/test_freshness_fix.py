"""Tests for build_id auto-injection and _default_build_id_safe.

AC-3: 所有 handler 的 build_id 自动注入，消除 perpetual freshness: stale
AC-3.1: _default_build_id_safe 在 BuildSnapshot 存在时返回最新 build_id
AC-3.2: _default_build_id_safe 在无 BuildSnapshot 时返回 None（不抛异常）
AC-3.3: handler 请求未显式传 build_id 时自动解析
"""
import pytest
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn_with_build_snapshot(tmp_db_path):
    """Database with two BuildSnapshots to test latest-id resolution."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b_old', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '', index_store_path: '', "
        "toolchain_id: 'Xcode15.4', commit_sha: '', build_config_hash: 'h1', "
        "created_at: '2026-06-01'})"
    )
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b_latest', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '', index_store_path: '', "
        "toolchain_id: 'Xcode15.4', commit_sha: '', build_config_hash: 'h1', "
        "created_at: '2026-06-24'})"
    )
    yield conn
    conn.close()


@pytest.fixture
def conn_empty(tmp_db_path):
    """Database without any BuildSnapshot."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    yield conn
    conn.close()


# ── AC-3.1: latest build_id ──────────────────────────────────────
def test_default_build_id_safe_returns_latest(conn_with_build_snapshot):
    """When BuildSnapshots exist, returns the most recent one's ID."""
    from orchard.server import _default_build_id_safe
    build_id = _default_build_id_safe(conn_with_build_snapshot, "")
    assert build_id == "b_latest"


def test_default_build_id_safe_returns_none_when_no_snapshots(conn_empty):
    """When no BuildSnapshots exist, returns None without raising."""
    from orchard.server import _default_build_id_safe
    build_id = _default_build_id_safe(conn_empty, "")
    assert build_id is None


# ── AC-3.3: handler receives build_id even when not explicitly passed ─
def test_find_callers_without_explicit_build_id_gets_one_injected(tmp_db_path):
    """AC-3.3: If build_id is NOT in args, the handler still gets one resolved."""
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b_auto', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '', index_store_path: '', "
        "toolchain_id: 'Xcode15.4', commit_sha: '', build_config_hash: 'h1', "
        "created_at: '2026-06-24'})"
    )
    conn.execute(
        "CREATE (:Target {id: 'T1', name: 'T1', platform: 'ios', sdk: '', "
        "triple: '', configuration: 'debug'})"
    )
    conn.execute(
        "MATCH (b:BuildSnapshot {id: 'b_auto'}), (t:Target {id: 'T1'}) "
        "CREATE (b)-[:BuiltTarget]->(t)"
    )
    # Create symbols + calls for find_callers
    for sym_id, name in [("s:A", "A"), ("s:B", "B")]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: 'swift.func', module: 'M', "
            f"target_id: 'T1', file_path: '/src/{name}.swift', signature: '', "
            f"container_usr: '', access_level: 'internal', origin: 'derived', "
            f"is_generated: false}})"
        )
    conn.execute(
        "MATCH (b:Symbol {id:'s:B'}), (a:Symbol {id:'s:A'}) "
        "CREATE (b)-[:Calls {source:'derived', confidence:1.0, provenance:'indexstore', "
        "build_id:'b_auto', reason:'source_direct'}]->(a)"
    )
    conn.close()

    # Monkey-patch _get_conn and test _do_handler build_id injection
    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = get_connection(tmp_db_path, read_only=True)

    try:
        result_json = server_mod._do_handler(
            "callers", "find_callers", "CallerRequest",
            {"usr": "s:A", "target_id": "T1"},  # no build_id!
        )
        import json
        result = json.loads(result_json)
        # When BuildSnapshot exists, response should NOT be "stale"
        assert result["freshness"] != "stale", (
            f"Expected fresh/build_mismatch/toolchain_mismatch but got {result['freshness']}"
        )
        # Result should still contain callers
        callers = result.get("data", [])
        assert len(callers) > 0
    finally:
        server_mod._conn.close()
        server_mod._conn = original_conn


def test_symbol_context_without_explicit_build_id_gets_one_injected(tmp_db_path):
    """AC-3.3: symbol_context handler also gets build_id auto-injected."""
    from orchard.graph.db import get_connection, init_schema

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b_auto', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '', index_store_path: '', "
        "toolchain_id: 'Xcode15.4', commit_sha: '', build_config_hash: 'h1', "
        "created_at: '2026-06-24'})"
    )
    conn.execute(
        "CREATE (:Target {id: 'T1', name: 'T1', platform: 'ios', sdk: '', "
        "triple: '', configuration: 'debug'})"
    )
    conn.execute(
        "MATCH (b:BuildSnapshot {id: 'b_auto'}), (t:Target {id: 'T1'}) "
        "CREATE (b)-[:BuiltTarget]->(t)"
    )
    conn.execute(
        "CREATE (:Symbol {id: 's:MyFunc', usr: 's:MyFunc', precise_id: 's:MyFunc', "
        "name: 'MyFunc()', language: 'swift', kind: 'swift.func', module: 'M', "
        "target_id: 'T1', file_path: '/src/f.swift', signature: 'func MyFunc()', "
        "container_usr: '', access_level: 'internal', origin: 'swift_symbolgraph', "
        "is_generated: false})"
    )
    conn.close()

    import orchard.server as server_mod

    original_conn = server_mod._conn
    server_mod._conn = get_connection(tmp_db_path, read_only=True)

    try:
        result_json = server_mod._do_handler(
            "symbol_context", "get_symbol_context", "SymbolContextRequest",
            {"usr": "s:MyFunc", "target_id": "T1"},  # no build_id!
        )
        import json
        result = json.loads(result_json)
        assert result["freshness"] != "stale", (
            f"Expected non-stale but got {result['freshness']}"
        )
        assert result["data"] is not None
        assert result["data"]["name"] == "MyFunc()"
    finally:
        server_mod._conn.close()
        server_mod._conn = original_conn
