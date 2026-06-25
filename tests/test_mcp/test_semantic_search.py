"""Tests for semantic_search MCP handler."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from orchard.graph.db import get_connection, init_schema
from orchard.mcp.handlers.semantic_search import (
    SemanticSearchRequest,
    dot,
    norm,
    semantic_search,
)


def _make_vec768(base: float = 0.0, step: float = 0.001) -> list[float]:
    """Generate a 768-element vector for Ladybug FLOAT[768] columns."""
    return [base + i * step for i in range(768)]


@pytest.fixture
def conn_with_chunks(tmp_db_path):
    """Seed Symbol + Chunk nodes for semantic search tests."""
    conn = get_connection(tmp_db_path)
    init_schema(conn)

    # Seed Symbol nodes
    for sid, usr, name, kind, module in [
        ("MyApp:s:Foo", "s:Foo", "Foo", "struct", "MyApp"),
        ("MyApp:s:doIt", "s:doIt()", "doIt", "function", "MyApp"),
        ("MyApp:s:empty", "s:empty", "emptyFunc", "function", "MyApp"),
    ]:
        conn.execute(
            "CREATE (:Symbol {"
            f"id: '{sid}', usr: '{usr}', precise_id: '', "
            f"name: '{name}', language: 'swift', kind: '{kind}', "
            f"module: '{module}', target_id: 'MyApp', file_path: '', "
            f"signature: '', container_usr: '', access_level: 'public', "
            f"origin: 'symbolgraph', is_generated: false"
            "})"
        )

    # Seed Chunk nodes (without embedding for FTS path)
    chunks = [
        ("MyApp:s:Foo:chunk:type:0", "s:Foo", "type",
         "struct Foo: public struct Foo"),
        ("MyApp:s:doIt:chunk:method:1", "s:doIt()", "method",
         "function doIt: func doIt()"),
        ("MyApp:s:Foo:chunk:type:vec", "s:Foo", "type",
         "struct Foo: public struct Foo with generics"),
    ]
    for cid, usr, kind, content in chunks:
        conn.execute(
            "CREATE (:Chunk {"
            f"id: '{cid}', owner_usr: '{usr}', "
            f"chunk_kind: '{kind}', content: '{content}'"
            "})"
        )

    # Seed a Chunk WITH embedding for vector search path.
    # Must be exactly 768 elements for Ladybug FLOAT[768].
    stored_vec = _make_vec768(base=0.0, step=0.001)
    conn.execute(
        "CREATE (:Chunk {"
        "id: $cid, owner_usr: $usr, chunk_kind: $kind, "
        "content: $content, embedding: $emb"
        "})",
        {
            "cid": "MyApp:s:Foo:chunk:type:vec_emb",
            "usr": "s:Foo",
            "kind": "type",
            "content": "struct Foo: vector-searchable content",
            "emb": stored_vec,
        },
    )

    yield conn
    conn.close()


class TestDotNorm:
    """Tests for dot-product and norm helper functions."""

    def test_dot_simple(self):
        assert dot([1, 2, 3], [4, 5, 6]) == 32  # 1*4 + 2*5 + 3*6

    def test_dot_zero_vectors(self):
        assert dot([0, 0], [1, 2]) == 0

    def test_norm_simple(self):
        assert norm([3, 4]) == 5.0

    def test_norm_zero(self):
        assert norm([0, 0, 0]) == 0.0

    def test_cosine_similarity_identical(self):
        v = [1.0, 2.0, 3.0]
        score = dot(v, v) / (norm(v) * norm(v))
        assert math.isclose(score, 1.0, rel_tol=1e-9)

    def test_cosine_similarity_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        score = dot(a, b) / (norm(a) * norm(b))
        assert math.isclose(score, 0.0, abs_tol=1e-9)


class TestSemanticSearchFTS:
    """Tests for FTS fallback path (no Ollama / no embedding)."""

    def test_fts_finds_matching_chunk(self, conn_with_chunks):
        """Substring match should find the Foo chunk."""
        req = SemanticSearchRequest(query="struct Foo", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        assert len(resp.data) >= 2  # multiple Foo chunks with "struct Foo"
        usrs = {item["usr"] for item in resp.data}
        assert "s:Foo" in usrs

    def test_fts_finds_doit_chunk(self, conn_with_chunks):
        """Substring match for doIt should find the function chunk."""
        req = SemanticSearchRequest(query="doIt", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        assert len(resp.data) >= 1
        usrs = {item["usr"] for item in resp.data}
        assert "s:doIt()" in usrs

    def test_fts_case_insensitive(self, conn_with_chunks):
        """Search should be case-insensitive."""
        req = SemanticSearchRequest(query="FUNC", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        assert len(resp.data) >= 1
        assert any("doIt" in item["chunk_content"] for item in resp.data)

    def test_fts_no_match_returns_empty(self, conn_with_chunks):
        """A query with no matches returns empty data and open_gaps."""
        req = SemanticSearchRequest(query="nonexistent_xyz", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        assert resp.data == []
        assert "no matching chunks found" in resp.open_gaps

    def test_fts_respects_top_k(self, conn_with_chunks):
        """top_k should limit results."""
        req = SemanticSearchRequest(query="struct", top_k=1)
        resp = semantic_search(conn_with_chunks, req)

        assert len(resp.data) == 1


class TestSemanticSearchVector:
    """Tests for the vector (embedding) path."""

    def test_vector_search_with_mocked_embedder(self, conn_with_chunks):
        """When Embedder succeeds, cosine similarity on stored vectors is used."""
        # Return the exact same vector stored in the DB for cosine ~= 1.0
        stored_vec = _make_vec768(base=0.0, step=0.001)

        with patch(
            "orchard.search.embedder.Embedder",
            autospec=True,
        ) as mock_embedder_cls:
            mock_embedder = mock_embedder_cls.return_value
            mock_embedder.embed.return_value = stored_vec

            req = SemanticSearchRequest(query="vector", top_k=10)
            resp = semantic_search(conn_with_chunks, req)

            assert len(resp.data) >= 1
            scores = [item["score"] for item in resp.data]
            # The exact-match vector should have cosine similarity ~1.0
            assert any(math.isclose(s, 1.0, rel_tol=0.01) for s in scores)

    def test_vector_search_falls_back_when_embedder_fails(self, conn_with_chunks):
        """When Embedder raises, gracefully degrade to FTS."""
        with patch(
            "orchard.search.embedder.Embedder",
            autospec=True,
        ) as mock_embedder_cls:
            mock_embedder = mock_embedder_cls.return_value
            mock_embedder.embed.side_effect = RuntimeError("Ollama down")

            req = SemanticSearchRequest(query="struct Foo", top_k=10)
            resp = semantic_search(conn_with_chunks, req)

            assert len(resp.data) >= 1
            usrs = {item["usr"] for item in resp.data}
            assert "s:Foo" in usrs


class TestSemanticSearchResponseShape:
    """Tests for the response structure."""

    def test_response_fields(self, conn_with_chunks):
        """Each result item should have the expected fields."""
        req = SemanticSearchRequest(query="doIt", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        item = resp.data[0]
        assert "usr" in item
        assert "name" in item
        assert "kind" in item
        assert "module" in item
        assert "chunk_content" in item
        assert "chunk_kind" in item
        assert "score" in item
        assert isinstance(item["score"], float)

    def test_resolves_symbol_name(self, conn_with_chunks):
        """The owning Symbol's name/kind/module should be resolved."""
        req = SemanticSearchRequest(query="doIt", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        do_it_items = [i for i in resp.data if i["usr"] == "s:doIt()"]
        assert len(do_it_items) == 1
        assert do_it_items[0]["name"] == "doIt"
        assert do_it_items[0]["kind"] == "function"
        assert do_it_items[0]["module"] == "MyApp"

    def test_freshness_included(self, conn_with_chunks):
        """Response should include freshness status."""
        req = SemanticSearchRequest(query="Foo", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        assert resp.freshness in ("fresh", "stale", "partially_stale",
                                   "build_mismatch", "toolchain_mismatch")

    def test_evidence_sources(self, conn_with_chunks):
        """Response should include evidence sources."""
        req = SemanticSearchRequest(query="Foo", top_k=10)
        resp = semantic_search(conn_with_chunks, req)

        assert "embedding_projection" in resp.evidence_sources
        assert "semantic_search" in resp.evidence_sources
