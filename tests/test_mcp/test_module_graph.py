"""Tests for get_module_graph handler."""
import pytest
from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.module_graph import (
    ModuleGraphRequest,
    get_module_graph,
)


@pytest.fixture
def conn_with_modules(tmp_db_path):
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    # Seed Module nodes + DependsOn edges.
    for name, lang in [("UIKit", "swift"), ("DataLayer", "swift"), ("Service", "swift")]:
        conn.execute("MERGE (m:Module {name: $name}) SET m.language=$lang",
                     {"name": name, "lang": lang})
    conn.execute("CREATE REL TABLE IF NOT EXISTS DependsOn("
                 "  FROM Module TO Module, source STRING, build_id STRING)")
    conn.execute(
        "MATCH (a:Module {name: 'UIKit'}), (b:Module {name: 'DataLayer'}) "
        "MERGE (a)-[:DependsOn {source: 'derive/architecture', build_id: 'b1'}]->(b)"
    )
    conn.execute(
        "MATCH (a:Module {name: 'DataLayer'}), (b:Module {name: 'Service'}) "
        "MERGE (a)-[:DependsOn {source: 'derive/architecture', build_id: 'b1'}]->(b)"
    )
    yield conn
    conn.close()


def test_get_module_graph_all(conn_with_modules):
    """Returns all modules and their DependsOn edges."""
    req = ModuleGraphRequest(build_id="b1")
    resp = get_module_graph(conn_with_modules, req)
    assert resp.data["module_count"] == 3
    assert resp.data["edge_count"] == 2
    names = {m["name"] for m in resp.data["modules"]}
    assert names == {"UIKit", "DataLayer", "Service"}


def test_get_module_graph_filter(conn_with_modules):
    """Module filter restricts results."""
    req = ModuleGraphRequest(build_id="b1", module_filter="UI")
    resp = get_module_graph(conn_with_modules, req)
    assert resp.data["module_count"] == 1
    assert resp.data["modules"][0]["name"] == "UIKit"
    # Edges are also filtered by the module name.
    assert resp.data["edge_count"] == 1


def test_get_module_graph_no_deps(conn_with_modules):
    """With include_deps=False, only modules are returned."""
    req = ModuleGraphRequest(build_id="b1", include_deps=False)
    resp = get_module_graph(conn_with_modules, req)
    assert resp.data["module_count"] == 3
    assert resp.data["edge_count"] == 0


def test_get_module_graph_empty(conn_with_modules):
    """Non-matching filter returns empty."""
    req = ModuleGraphRequest(build_id="b1", module_filter="ZZZ")
    resp = get_module_graph(conn_with_modules, req)
    assert resp.data["module_count"] == 0
    assert "no modules found" in resp.open_gaps
