from orchard.setup import _ORCHARD_BLOCK, _setup_claude_md


def _render_block() -> str:
    return _ORCHARD_BLOCK.format(project_name="Demo")


def test_orchard_block_mentions_single_frame_boundary():
    block = _render_block()

    assert "orchard_lookup_frame" in block
    assert "single stack frame" in block
    assert "full crashlogs are handled outside Orchard" in block
    assert "explicit symbol identity" in block
    assert len(block.splitlines()) <= 90


def test_orchard_block_excludes_crash_thread_analyzer_language():
    block = _render_block().lower()

    forbidden = [
        "orchard_lookup_crash_thread",
        "crashed-thread",
        "crashed thread",
        "crash triage",
        "first indexed business symbol",
        "business_first_frame",
        "thread/dispatch boundaries",
        "dispatch_boundaries",
        "arm64",
        "x0 = 0",
        "arm64_null_this",
        "likely_fault",
        "root_cause",
        "delegate selector inference",
    ]
    for text in forbidden:
        assert text not in block


def test_orchard_block_keeps_graph_context_labels():
    block = _render_block()

    assert "call_style" in block
    assert "execution_boundary" in block
    assert "source_scope" in block
    assert "outside_workspace_root" in block
    assert "data.summary" in block
    assert "exact C++ object field offsets" in block
    assert "orchard_class_layout" not in block
    assert "## Graph Schema" not in block


def test_setup_claude_md_injects_block_without_database(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    ok, msg = _setup_claude_md(project_dir)

    assert ok is True
    assert "injected orchard block" in msg
    assert "stats unavailable before first ingest" not in (project_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert "stats unavailable before first ingest" not in (project_dir / "AGENTS.md").read_text(encoding="utf-8")
