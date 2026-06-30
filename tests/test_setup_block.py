from orchard.setup import _ORCHARD_BLOCK


def test_orchard_block_mentions_crash_thread_lookup():
    block = _ORCHARD_BLOCK.format(
        project_name="Demo",
        symbol_count=1,
        calls_count=2,
        contains_count=3,
    )

    assert "orchard_lookup_frame" in block
    assert "resolve owner/method candidates, direct callers, and next actions" in block
    assert "orchard_lookup_crash_thread" in block
    assert "first indexed business symbol" in block
    assert "dispatch boundaries" in block
