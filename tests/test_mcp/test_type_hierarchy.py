import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.handlers.type_hierarchy import get_type_hierarchy, TypeHierarchyRequest


@pytest.fixture
def conn_with_hierarchy(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    for sym_id, name, kind in [
        ("T1:s:Base", "Base", "swift.class"),
        ("T1:s:Child", "Child", "swift.class"),
        ("T1:s:Proto", "MyProtocol", "swift.protocol"),
    ]:
        conn.execute(
            f"CREATE (:Symbol {{id: '{sym_id}', usr: 's:{name}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: '{kind}', module: 'M', "
            f"target_id: 'T1', file_path: '', signature: '', container_usr: '', "
            f"access_level: 'public', origin: 'swift_symbolgraph', is_generated: false}})"
        )
    conn.execute(
        "MATCH (c:Symbol {id:'T1:s:Child'}), (b:Symbol {id:'T1:s:Base'}) "
        "CREATE (c)-[:Inherits {source:'swift_symbolgraph'}]->(b)"
    )
    conn.execute(
        "MATCH (c:Symbol {id:'T1:s:Child'}), (p:Symbol {id:'T1:s:Proto'}) "
        "CREATE (c)-[:ConformsTo {source:'swift_symbolgraph'}]->(p)"
    )
    yield conn
    conn.close()


def test_get_type_hierarchy_parents(conn_with_hierarchy):
    req = TypeHierarchyRequest(usr="s:Child", target_id="T1", build_id="b1")
    resp = get_type_hierarchy(conn_with_hierarchy, req)
    parent_names = {p["name"] for p in resp.data["parents"]}
    assert "Base" in parent_names


def test_get_type_hierarchy_protocols(conn_with_hierarchy):
    req = TypeHierarchyRequest(usr="s:Child", target_id="T1", build_id="b1")
    resp = get_type_hierarchy(conn_with_hierarchy, req)
    proto_names = {p["name"] for p in resp.data["protocols"]}
    assert "MyProtocol" in proto_names


def test_get_type_hierarchy_children(conn_with_hierarchy):
    req = TypeHierarchyRequest(usr="s:Base", target_id="T1", build_id="b1")
    resp = get_type_hierarchy(conn_with_hierarchy, req)
    child_names = {c["name"] for c in resp.data["children"]}
    assert "Child" in child_names
