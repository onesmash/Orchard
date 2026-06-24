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
        "build_config_hash: 'hash1', created_at: '2026-06-24T00:00:00', "
        "sdk: 'macosx14.5', configuration: 'debug'})"
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


def test_freshness_reads_sdk_configuration_created_at(tmp_db_path):
    """BuildSnapshot sdk/configuration/created_at are persisted and surfaced."""
    from orchard.build.context import BuildContext, make_build_id
    from orchard.normalize.identity import upsert_build_snapshot

    conn = get_connection(tmp_db_path)
    init_schema(conn)
    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/tmp/pkg", scheme=None, target="MyLib",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path="/tmp/dd/IndexStore",
        symbolgraph_output_path=None, commit_sha=None, build_config_hash="abc",
    )
    ctx.build_id = make_build_id(ctx)
    upsert_build_snapshot(conn, ctx)

    snapshot, status = freshness_for(conn, ctx.build_id, {})
    assert status == "fresh"
    assert snapshot.sdk == ctx.sdk
    assert snapshot.configuration == ctx.configuration
    assert snapshot.created_at and len(snapshot.created_at) > 0
    conn.close()
