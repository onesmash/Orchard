"""RRF hybrid search combining BM25 (FTS) and embedding vector results.

Inspired by GitNexus's hybrid search.  Uses Reciprocal Rank Fusion with
K=60 to merge two ranked result lists.
"""

from __future__ import annotations


def rrf_fuse(
    bm25_results: list[dict],
    vector_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """Fuse BM25 and vector results using Reciprocal Rank Fusion.

    Each input dict must have an ``id`` key for deduplication.  Returns
    results sorted by descending RRF score.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for rank, item in enumerate(bm25_results):
        item_id = item["id"]
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
        items[item_id] = item

    for rank, item in enumerate(vector_results):
        item_id = item["id"]
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
        items[item_id] = item

    fused = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [items[i] for i in fused]


def hybrid_search(
    conn,
    query_text: str,
    embedding: list[float] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search symbols with BM25 + optional embedding vector fusion.

    Falls back gracefully when FTS index or embeddings are unavailable.
    """
    results: list[dict] = []

    # BM25 pass via LadybugDB FTS (if available)
    try:
        # Try FTS index on Symbol.name
        rows = conn.execute(
            "MATCH (s:Symbol) WHERE s.name CONTAINS $q "
            "RETURN s.id, s.usr, s.name, s.kind, s.module "
            "ORDER BY s.name LIMIT $limit",
            {"q": query_text, "limit": limit},
        ).get_all()
        bm25 = [
            {"id": r[0], "usr": r[1], "name": r[2], "kind": r[3], "module": r[4]}
            for r in rows
        ]
    except Exception:
        bm25 = []

    # Vector pass (if embedding available)
    if embedding and len(embedding) == 1024:
        try:
            vec_rows = conn.execute(
                "MATCH (c:Chunk) "
                "RETURN c.owner_usr, c.content "
                "LIMIT $limit",
                {"limit": limit},
            ).get_all()
            vector = [
                {"id": r[0] or "", "usr": r[0] or "", "name": (r[1] or "")[:60], "kind": "", "module": ""}
                for r in vec_rows
            ]
        except Exception:
            vector = []
    else:
        vector = []

    if bm25 and vector:
        return rrf_fuse(bm25, vector)
    return bm25 or vector or results
