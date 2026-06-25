"""Tests for find_layer_violations handler."""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.handlers.layer_violations import (
    LayerViolationRequest,
    find_layer_violations,
)


@pytest.fixture
def conn_with_layered_calls(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "T"

    # Seed symbols in UI, Data, and Service modules.
    syms = [
        ("T:s:ui1", "s:ui1", "renderButton", "UIKit", "swift"),
        ("T:s:ui2", "s:ui2", "showAlert", "WidgetKit", "swift"),
        ("T:s:data1", "s:data1", "fetchUsers", "DataLayer", "swift"),
        ("T:s:data2", "s:data2", "saveUser", "Repository", "swift"),
        ("T:s:svc1", "s:svc1", "authenticate", "AuthService", "swift"),
        ("T:s:svc2", "s:svc2", "sendRequest", "NetworkAPI", "swift"),
        ("T:s:common", "s:common", "formatString", "CommonUtil", "swift"),
    ]
    for sid, usr, name, mod, lang in syms:
        conn.execute(
            "MERGE (s:Symbol {id: $id}) "
            "SET s.usr=$usr, s.precise_id='', s.name=$name, s.language=$lang, "
            "s.kind='function', s.module=$mod, s.target_id=$tid, s.file_path='', "
            "s.signature='', s.container_usr='', s.access_level='public', "
            "s.origin='test', s.is_generated=false",
            {"id": sid, "usr": usr, "name": name, "mod": mod, "lang": lang, "tid": target_id},
        )

    # Create Calls edges:
    # Violation: UI -> Data
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:ui1'}), (b:Symbol {id: 'T:s:data1'}) "
        "MERGE (a)-[:Calls {source: 'test', build_id: 'b1'}]->(b)"
    )
    # Violation: UI -> Data (second one)
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:ui2'}), (b:Symbol {id: 'T:s:data2'}) "
        "MERGE (a)-[:Calls {source: 'test', build_id: 'b1'}]->(b)"
    )
    # Violation: Data -> Service
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:data1'}), (b:Symbol {id: 'T:s:svc1'}) "
        "MERGE (a)-[:Calls {source: 'test', build_id: 'b1'}]->(b)"
    )
    # OK: Service -> Data (normal downward flow)
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:svc1'}), (b:Symbol {id: 'T:s:data2'}) "
        "MERGE (a)-[:Calls {source: 'test', build_id: 'b1'}]->(b)"
    )
    # OK: within same layer
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:ui1'}), (b:Symbol {id: 'T:s:ui2'}) "
        "MERGE (a)-[:Calls {source: 'test', build_id: 'b1'}]->(b)"
    )
    # OK: unknown module -> anything
    conn.execute(
        "MATCH (a:Symbol {id: 'T:s:common'}), (b:Symbol {id: 'T:s:data1'}) "
        "MERGE (a)-[:Calls {source: 'test', build_id: 'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_find_layer_violations_ui_to_data(conn_with_layered_calls):
    """Detects UI -> Data violations."""
    req = LayerViolationRequest(target_id="T", build_id="b1")
    resp = find_layer_violations(conn_with_layered_calls, req)
    assert resp.data["total"] == 3
    assert resp.data["by_pattern"] == {
        "UI→Data": 2,
        "Data→Service": 1,
    }
    ui_data = [v for v in resp.data["violations"] if v["pattern"] == "UI→Data"]
    assert len(ui_data) == 2
    # Verify details.
    assert ui_data[0]["caller_module"] == "UIKit"
    assert ui_data[0]["callee_module"] == "DataLayer"
    assert ui_data[0]["caller_usr"] == "s:ui1"
    assert ui_data[0]["callee_name"] == "fetchUsers"


def test_find_layer_violations_no_details(conn_with_layered_calls):
    """With include_details=False, USR/name are omitted."""
    req = LayerViolationRequest(target_id="T", build_id="b1", include_details=False)
    resp = find_layer_violations(conn_with_layered_calls, req)
    assert resp.data["total"] == 3
    v = resp.data["violations"][0]
    assert "caller_usr" not in v
    assert "caller_name" not in v


def test_find_layer_violations_none(tmp_db_path):
    """No matching target returns empty violations."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Empty DB — no symbols, no calls.
    req = LayerViolationRequest(target_id="NoSuchTarget", build_id="b1")
    resp = find_layer_violations(conn, req)
    assert resp.data["total"] == 0
    assert "no layer violations found" in resp.open_gaps
    conn.close()
