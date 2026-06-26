import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.handlers.symbol_context import get_symbol_context, SymbolContextRequest


@pytest.fixture
def conn_with_symbol(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    conn.execute(
        "CREATE (:Symbol {id: 's:MyFunc', usr: 's:MyFunc', precise_id: 's:MyFunc', "
        "name: 'MyFunc()', language: 'swift', kind: 'swift.func', module: 'MyModule', "
        "target_id: 'T1', file_path: '/src/f.swift', signature: 'func MyFunc()', "
        "container_usr: '', access_level: 'internal', origin: 'swift_symbolgraph', "
        "is_generated: false})"
    )
    conn.execute(
        "CREATE (:BuildSnapshot {id: 'b1', build_system: 'xcodebuild', "
        "workspace_root: '/app', derived_data_path: '', index_store_path: '', "
        "toolchain_id: 'Xcode15.4', commit_sha: '', build_config_hash: 'h1', "
        "created_at: '2026-06-24'})"
    )
    yield conn
    conn.close()


def test_get_symbol_context_found(conn_with_symbol):
    req = SymbolContextRequest(usr="s:MyFunc", target_id="T1", build_id="b1")
    resp = get_symbol_context(conn_with_symbol, req)
    assert resp.data["name"] == "MyFunc()"
    assert resp.data["language"] == "swift"
    assert resp.freshness in ("fresh", "stale", "build_mismatch", "toolchain_mismatch", "partially_stale")
    assert isinstance(resp.evidence_sources, list)


def test_get_symbol_context_not_found(conn_with_symbol):
    req = SymbolContextRequest(usr="s:Missing", target_id="T1", build_id="b1")
    resp = get_symbol_context(conn_with_symbol, req)
    assert resp.data is None
    assert "not found" in resp.open_gaps[0].lower()
