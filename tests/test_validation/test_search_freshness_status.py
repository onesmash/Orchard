from orchard.validation.freshness import map_search_freshness


def test_map_search_freshness_returns_unknown_for_unrecognized_status():
    assert map_search_freshness("mystery") == "unknown"


def test_map_search_freshness_keeps_stale_and_fresh_values():
    assert map_search_freshness("fresh") == "fresh"
    assert map_search_freshness("stale") == "stale"
