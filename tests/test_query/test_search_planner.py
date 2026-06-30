from orchard.query.search_contract import SearchStatus
from orchard.query.search_planner import (
    classify_search_query,
    plan_search_next_actions,
    rank_symbol_candidates,
)
from orchard.validation.freshness import map_search_freshness


def test_map_search_freshness_keeps_phase1_values_small():
    assert map_search_freshness("fresh") == "fresh"
    assert map_search_freshness("stale") == "stale"
    assert map_search_freshness("toolchain_mismatch") == "partially_stale"
    assert map_search_freshness("build_mismatch") == "partially_stale"


def test_classify_search_query_distinguishes_frame_like_input():
    assert classify_search_query("process_msg") == "symbol"
    assert classify_search_query("ssb::thread_wrapper_t") == "qualified_symbol"
    assert classify_search_query("ssb::thread_wrapper_t::process_msg(unsigned int)") == "frame"


def test_rank_symbol_candidates_prefers_owner_and_case_preserving_matches():
    rows = [
        {"usr": "u1", "name": "Process_Msg", "kind": "cxx.method", "language": "cxx", "module": "Core"},
        {"usr": "u2", "name": "process_msg", "kind": "cxx.method", "language": "cxx", "module": "Core"},
        {"usr": "u3", "name": "process_msgLater", "kind": "cxx.method", "language": "cxx", "module": "Core"},
    ]
    ranked = rank_symbol_candidates("process_msg", rows)
    assert [row["usr"] for row in ranked] == ["u2", "u1", "u3"]


def test_plan_search_next_actions_prefers_refresh_before_shell_fallback():
    status = SearchStatus(outcome="no_match", coverage="partial", freshness="stale")
    next_actions = plan_search_next_actions(
        status,
        {"symbols": [], "owners": ["thread_wrapper_t"], "text": ["process_msg"]},
        "process_msg",
    )
    assert next_actions[0]["tool"] == "orchard_refresh_index"
    assert next_actions[1]["tool"] == "orchard_search"
    assert next_actions[2]["tool"] == "shell_text_search"


def test_rank_symbol_candidates_is_stable_for_same_input():
    rows = [
        {"usr": "u2", "name": "process_msg", "kind": "cxx.method", "language": "cxx", "module": "Core"},
        {"usr": "u1", "name": "process_msg", "kind": "cxx.method", "language": "cxx", "module": "Alpha"},
    ]
    assert [row["usr"] for row in rank_symbol_candidates("process_msg", rows)] == ["u1", "u2"]
