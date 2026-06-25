"""semantic_search — hybrid vector + FTS semantic search over code chunks."""

from __future__ import annotations

import math
from dataclasses import dataclass

from orchard.mcp.handlers.base import BaseToolRequest, BaseToolResponse
from orchard.validation.freshness import freshness_for


def dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length float vectors."""
    return sum(x * y for x, y in zip(a, b))


def norm(v: list[float]) -> float:
    """L2 norm of a float vector."""
    return math.sqrt(sum(x * x for x in v))


@dataclass
class SemanticSearchRequest(BaseToolRequest):
    query: str = ""
    top_k: int = 10


def semantic_search(conn, req: SemanticSearchRequest) -> BaseToolResponse:
    """Hybrid semantic search over code chunks.

    1. Tries to vector-embed the query via :class:`Embedder`.
       Falls back to substring FTS if Ollama is unreachable.
    2. Vector path: finds Chunks with non-NULL embedding, computes
       cosine similarity in Python, returns top-k.
    3. FTS path: substring match on ``Chunk.content`` (case-insensitive).
    4. For each result, resolves the owning Symbol to return ``{usr, name, kind, module}``.
    """
    # 1. Try embedding
    query_vec: list[float] | None = None
    try:
        from orchard.search.embedder import Embedder

        query_vec = Embedder().embed(req.query)
    except Exception:
        pass

    # 2. Search
    results: list[tuple[float, tuple[str, str, str]]] = []
    if query_vec:
        q_norm = norm(query_vec)
        rows = conn.execute(
            "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
            "RETURN c.owner_usr, c.content, c.chunk_kind, c.embedding"
        ).get_all()
        for r in rows:
            emb = r[3]
            if emb and q_norm > 0:
                score = dot(query_vec, emb) / (q_norm * norm(emb))
                results.append((score, (r[0] or "", r[1] or "", r[2] or "")))
        results.sort(key=lambda x: x[0], reverse=True)
        results = results[: req.top_k]
    else:
        # FTS fallback: Ladybug CONTAINS substring match.
        # No FTS extension needed — CONTAINS is a built-in Cypher operator
        # that scans the column store efficiently (no Python-side O(N) loop).
        rows = conn.execute(
            "MATCH (c:Chunk) WHERE lower(c.content) CONTAINS lower($q) "
            "RETURN c.owner_usr, c.content, c.chunk_kind LIMIT $k",
            {"q": req.query, "k": req.top_k},
        ).get_all()
        for r in rows:
            results.append((1.0, (r[0] or "", r[1] or "", r[2] or "")))

    # 3. Resolve Symbol names
    data: list[dict] = []
    for score, (usr, content, chunk_kind) in results:
        sym_rows = conn.execute(
            "MATCH (s:Symbol {usr: $u}) RETURN s.name, s.kind, s.module LIMIT 1",
            {"u": usr},
        ).get_all()
        name = sym_rows[0][0] if sym_rows else usr
        sym_kind = sym_rows[0][1] if sym_rows else ""
        sym_module = sym_rows[0][2] if sym_rows else ""
        data.append(
            {
                "usr": usr,
                "name": name,
                "kind": sym_kind,
                "module": sym_module,
                "chunk_content": content,
                "chunk_kind": chunk_kind,
                "score": round(score, 4),
            }
        )

    _, freshness = freshness_for(conn, req.build_id or "", {})
    return BaseToolResponse(
        data=data,
        freshness=freshness,
        build_id=req.build_id,
        evidence_sources=["embedding_projection", "semantic_search"],
        open_gaps=[] if data else ["no matching chunks found"],
    )
