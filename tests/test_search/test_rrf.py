"""Tests for RRF hybrid search algorithm."""
from orchard.search.hybrid_search import rrf_fuse


def test_rrf_empty_both():
    assert rrf_fuse([], []) == []


def test_rrf_one_list_only():
    items = [{"id": "a", "name": "A"}]
    result = rrf_fuse(items, [])
    assert len(result) == 1
    assert result[0]["id"] == "a"


def test_rrf_fuses_two_lists():
    bm25 = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    vec = [{"id": "b", "name": "B"}, {"id": "c", "name": "C"}]
    result = rrf_fuse(bm25, vec)
    ids = [r["id"] for r in result]
    # b appears in both lists → highest score
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_rrf_different_lengths():
    bm25 = [{"id": str(i), "name": str(i)} for i in range(5)]
    vec = [{"id": str(i), "name": str(i)} for i in range(5, 10)]
    result = rrf_fuse(bm25, vec)
    assert len(result) == 10
