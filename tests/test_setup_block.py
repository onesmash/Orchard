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
    assert "thread/dispatch boundaries" in block


def test_orchard_block_mentions_crash_triage_annotations():
    block = _ORCHARD_BLOCK.format(
        project_name="Demo",
        symbol_count=1,
        calls_count=2,
        contains_count=3,
    )

    assert "x0 = 0" in block
    assert "arm64_null_this" in block
    assert "call_style" in block
    assert "execution_boundary" in block
    assert "source_scope" in block
    assert "outside_workspace_root" in block
    assert "data.summary" in block
    assert "exact C++ object field offsets" in block
    assert "orchard_class_layout" not in block
