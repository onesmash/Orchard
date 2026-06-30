from orchard.query.search_contract import SearchResponse, SearchStatus


def test_search_response_to_dict_keeps_compact_keys():
    resp = SearchResponse(
        query={"raw": "process_msg", "kind": "symbol"},
        status=SearchStatus(outcome="no_match", coverage="partial", freshness="stale"),
        matches=[],
        diag=["index_stale", "owner_search_recommended"],
        candidates={"symbols": [], "owners": ["thread_wrapper_t"], "text": ["process_msg"]},
        next_actions=[
            {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
            {"tool": "shell_text_search", "args": {"pattern": "process_msg"}},
        ],
    )

    assert resp.to_dict() == {
        "query": {"raw": "process_msg", "kind": "symbol"},
        "status": {
            "outcome": "no_match",
            "coverage": "partial",
            "freshness": "stale",
        },
        "matches": [],
        "diag": ["index_stale", "owner_search_recommended"],
        "candidates": {
            "symbols": [],
            "owners": ["thread_wrapper_t"],
            "text": ["process_msg"],
        },
        "next": [
            {"tool": "orchard_search", "args": {"name": "thread_wrapper_t"}},
            {"tool": "shell_text_search", "args": {"pattern": "process_msg"}},
        ],
    }


def test_search_response_rejects_unknown_freshness_value():
    try:
        SearchStatus(outcome="no_match", coverage="partial", freshness="broken")
    except ValueError as exc:
        assert "freshness" in str(exc)
    else:
        raise AssertionError("SearchStatus should reject unsupported freshness values")
