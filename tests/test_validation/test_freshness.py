import pytest
from orchard.validation.freshness import GraphFreshness, freshness_for
from orchard.graph.db import get_connection, init_schema


@pytest.fixture
def conn_with_snapshot(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '/dd', index_store_path: '/dd/is', "
        "toolchain_id: 'Xcode15.4', commit_sha: 'abc123', "
        "build_config_hash: 'hash1', created_at: '2026-06-24T00:00:00'})"
    )
    yield conn
    conn.close()


def test_freshness_fresh(conn_with_snapshot):
    snapshot, status = freshness_for(
        conn_with_snapshot, "b1",
        {"toolchain_id": "Xcode15.4", "build_config_hash": "hash1"},
    )
    assert status == "fresh"
    assert snapshot.build_id == "b1"


def test_freshness_toolchain_mismatch(conn_with_snapshot):
    _, status = freshness_for(
        conn_with_snapshot, "b1",
        {"toolchain_id": "Xcode16.0", "build_config_hash": "hash1"},
    )
    assert status == "toolchain_mismatch"


def test_freshness_build_mismatch(conn_with_snapshot):
    _, status = freshness_for(
        conn_with_snapshot, "b1",
        {"toolchain_id": "Xcode15.4", "build_config_hash": "hash2"},
    )
    assert status == "build_mismatch"


def test_freshness_no_snapshot(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    snapshot, status = freshness_for(conn, "nonexistent", {})
    assert status == "stale"
    conn.close()
