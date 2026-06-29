"""Real llama.cpp integration test: embed → store → search (no mocking).

Skips unless a GGUF embedding model is available.
"""

from __future__ import annotations

import math
import os
import tempfile

import pytest

from orchard.graph.db import get_connection, init_schema
from orchard.normalize.identity import upsert_symbols
from orchard.ingest.symbolgraph import SymbolRecord
from orchard.search.embedder import Embedder, EmbeddingError
from orchard.search.chunker import chunk_symbols
from orchard.handlers.semantic_search import SemanticSearchRequest, semantic_search


def _llama_available() -> bool:
    try:
        e = Embedder()
        v = e.embed("test")
        return len(v) == 1024
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _llama_available(),
    reason="GGUF embedding model not available (set ORCHARD_EMBED_MODEL or "
    "place qwen3-embedding-0.6b-Q4_K_M.gguf in ~/.orchard/models/)",
)


def test_real_llama_embed_dimension():
    """Real llama.cpp produces 1024-dim vectors."""
    e = Embedder()
    v = e.embed("function loadData: () -> Data")
    assert len(v) == 1024
    assert all(isinstance(x, float) for x in v)


def test_real_llama_batch_embed():
    """Real batch embedding returns one vector per input."""
    e = Embedder()
    vs = e.embed_batch(["struct Foo", "func bar()", "class Baz"])
    assert len(vs) == 3
    for v in vs:
        assert len(v) == 1024


def test_real_llama_cosine_semantic_similarity():
    """Semantically similar texts should have higher cosine similarity."""
    e = Embedder()

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na > 0 and nb > 0 else 0.0

    load_data = e.embed("func loadData() -> Data: fetch and parse remote data")
    fetch_json = e.embed("func fetchJSON() -> Data: download and decode JSON payload")
    render_view = e.embed("func renderView() -> some View: draw the main screen layout")

    sim_load_fetch = cosine(load_data, fetch_json)
    sim_load_render = cosine(load_data, render_view)

    assert sim_load_fetch > sim_load_render, (
        f"load→fetch {sim_load_fetch:.3f} should be > load→render {sim_load_render:.3f}"
    )
    assert sim_load_fetch > 0.5, f"load→fetch similarity too low: {sim_load_fetch:.3f}"


def test_real_llama_full_pipeline_embed_to_search():
    """Full E2E: seed Symbols → chunk → real embed → store → semantic_search."""
    conn = get_connection(os.path.join(tempfile.mkdtemp(), "graph.db"))
    init_schema(conn)

    upsert_symbols(
        conn,
        [
            SymbolRecord(
                usr="s:loadData", precise_id="", name="loadData",
                kind="function", module="Data", language="swift",
                file_path="/src/Data.swift", signature="()->Data",
                access_level="public", container_usr=None,
            ),
            SymbolRecord(
                usr="s:renderView", precise_id="", name="renderView",
                kind="function", module="UI", language="swift",
                file_path="/src/UI.swift", signature="()->some View",
                access_level="public", container_usr=None,
            ),
            SymbolRecord(
                usr="s:formatDate", precise_id="", name="formatDate",
                kind="function", module="Util", language="swift",
                file_path="/src/Util.swift", signature="(Date)->String",
                access_level="public", container_usr=None,
            ),
        ],
        scope_id="LlamaTest",
    )

    chunks = chunk_symbols(conn, "LlamaTest")
    embedder = Embedder()
    vectors = embedder.embed_batch([c.content for c in chunks])
    for chunk, vec in zip(chunks, vectors):
        conn.execute(
            "MERGE (c:Chunk {id: $id}) SET c.owner_usr=$usr, "
            "c.chunk_kind=$kind, c.content=$content, c.embedding=$emb",
            {
                "id": chunk.chunk_id, "usr": chunk.owner_usr,
                "kind": chunk.chunk_kind, "content": chunk.content,
                "emb": vec,
            },
        )

    resp = semantic_search(
        conn, SemanticSearchRequest(query="load remote data", top_k=10))
    assert len(resp.data) >= 1
    assert resp.data[0]["usr"] == "s:loadData", (
        f"expected loadData first, got {resp.data[0]['usr']}"
    )
    assert resp.data[0]["score"] > 0.5, (
        f"score too low: {resp.data[0]['score']}"
    )

    assert resp.freshness is not None
    assert "embedding_projection" in resp.evidence_sources
    assert "semantic_search" in resp.evidence_sources

    conn.close()


@pytest.mark.asyncio
async def test_real_llama_embedding_projection_pipeline_integration(tmp_path):
    """Verify the full pipeline phase works with real llama.cpp."""
    from unittest.mock import patch
    from orchard.pipeline.runner import run_ingest_pipeline
    from orchard.build.context import BuildContext, make_build_id

    ctx = BuildContext(
        build_id="", build_system="swift_build",
        workspace_root="/tmp/pkg", scheme=None, target="LlamaTest",
        configuration="debug", sdk="macosx14.5",
        triple="arm64-apple-macosx14.5", toolchain_id="swift-5.10",
        derived_data_path="/tmp/dd", index_store_path=None,
        symbolgraph_output_path=None, commit_sha=None, build_config_hash="abc",
    )
    ctx.build_id = make_build_id(ctx)

    from orchard.ingest.indexstore import IndexStoreResult
    from orchard.ingest.symbolgraph import SymbolGraphResult

    sg = SymbolGraphResult(symbols=[
        SymbolRecord(usr="s:A", precise_id="", name="viewA",
                     kind="struct", module="M", language="swift",
                     file_path=None, signature=None, access_level="public",
                     container_usr=None),
    ], relationships=[])

    db_path = str(tmp_path / "graph.db")
    with (
        patch("orchard.pipeline.runner.read_index_store", return_value=(IndexStoreResult(), None)),
        patch("orchard.pipeline.runner.parse_symbolgraph", return_value=sg),
        patch("orchard.pipeline.runner.discover_symbolgraph_paths", return_value=["/x.json"]),
    ):
        results = await run_ingest_pipeline(ctx, db_path=db_path)

    phases = {r.phase: r.stats for r in results}
    assert "embedding_projection" in phases
    ep = phases["embedding_projection"]
    assert ep["embedded"] >= 1, f"expected >=1 embedded, got {ep}"
    assert ep["chunks"] >= 1
