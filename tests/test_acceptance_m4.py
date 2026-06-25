"""M4 acceptance tests: semantic_search + module_graph + layer_violations."""
from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols, make_symbol_id
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.derive.architecture import run_architecture_derivation
from orchard.mcp.handlers.semantic_search import SemanticSearchRequest, semantic_search
from orchard.mcp.handlers.module_graph import ModuleGraphRequest, get_module_graph
from orchard.mcp.handlers.layer_violations import (
    LayerViolationRequest,
    find_layer_violations,
)


def test_m4_semantic_search_fts_fallback(tmp_db_path):
    """semantic_search returns FTS results when Chunks exist (no embedding needed)."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "M4Target"

    # Seed Symbols + Chunks
    upsert_symbols(
        conn,
        [
            SymbolRecord(
                usr="s:loadData", precise_id="", name="loadData",
                kind="function", module="DataLayer", language="swift",
                file_path="/src/D.swift", signature="()->Data",
                access_level="public", container_usr=None,
            ),
            SymbolRecord(
                usr="s:renderView", precise_id="", name="renderView",
                kind="function", module="UILayer", language="swift",
                file_path="/src/U.swift", signature="()->View",
                access_level="public", container_usr=None,
            ),
        ],
        target_id,
    )

    conn.execute(
        "CREATE (:Chunk {id: 'c1', owner_usr: 's:loadData', "
        "chunk_kind: 'method', content: 'function loadData: loads remote data'})"
    )
    conn.execute(
        "CREATE (:Chunk {id: 'c2', owner_usr: 's:renderView', "
        "chunk_kind: 'method', content: 'function renderView: draws the UI'})"
    )

    # semantic_search (FTS path, no Ollama)
    resp = semantic_search(conn, SemanticSearchRequest(query="loadData", build_id="m4"))
    assert len(resp.data) >= 1
    assert resp.data[0]["usr"] == "s:loadData"
    assert resp.freshness is not None
    assert "semantic_search" in resp.evidence_sources

    conn.close()


def test_m4_architecture_and_module_graph(tmp_db_path):
    """architecture_derivation + get_module_graph end-to-end."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "M4Target"

    upsert_symbols(
        conn,
        [
            SymbolRecord(
                usr="s:uiFunc", precise_id="", name="uiFunc",
                kind="function", module="UIModule", language="swift",
                file_path="", signature="", access_level="public",
                container_usr=None,
            ),
            SymbolRecord(
                usr="s:dataFunc", precise_id="", name="dataFunc",
                kind="function", module="DataModule", language="swift",
                file_path="", signature="", access_level="public",
                container_usr=None,
            ),
        ],
        target_id,
    )
    # uiFunc calls dataFunc (cross-module)
    conn.execute(
        "MATCH (a:Symbol {id: $a}), (b:Symbol {id: $b}) "
        "CREATE (a)-[:Calls {source:'test', confidence:1.0}]->(b)",
        {"a": make_symbol_id(target_id, "s:uiFunc"),
         "b": make_symbol_id(target_id, "s:dataFunc")},
    )

    stats = run_architecture_derivation(conn, target_id, build_id="m4")
    assert stats["module_deps"] >= 1

    # get_module_graph
    resp = get_module_graph(conn, ModuleGraphRequest(build_id="m4"))
    edges = resp.data["edges"]
    assert len(edges) >= 1
    assert any(e["source_module"] == "UIModule" and e["target_module"] == "DataModule" for e in edges)
    assert resp.freshness is not None

    conn.close()


def test_m4_layer_violations(tmp_db_path):
    """find_layer_violations detects UI→Data crossing."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)
    target_id = "M4Target"

    upsert_symbols(
        conn,
        [
            SymbolRecord(
                usr="s:render", precise_id="", name="render",
                kind="function", module="UIWidget", language="swift",
                file_path="", signature="", access_level="public",
                container_usr=None,
            ),
            SymbolRecord(
                usr="s:fetch", precise_id="", name="fetch",
                kind="function", module="DataStore", language="swift",
                file_path="", signature="", access_level="public",
                container_usr=None,
            ),
        ],
        target_id,
    )
    conn.execute(
        "MATCH (a:Symbol {id: $a}), (b:Symbol {id: $b}) "
        "CREATE (a)-[:Calls {source:'test', confidence:1.0}]->(b)",
        {"a": make_symbol_id(target_id, "s:render"),
         "b": make_symbol_id(target_id, "s:fetch")},
    )

    resp = find_layer_violations(
        conn, LayerViolationRequest(build_id="m4"))
    # heuristic: "ui" in module + "data" in module → violation
    violations = resp.data["violations"]
    assert len(violations) >= 1
    assert violations[0]["pattern"] == "UI→Data"
    assert resp.freshness is not None

    conn.close()
