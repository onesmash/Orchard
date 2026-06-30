from orchard.graph.db import get_connection
from orchard.setup import _ORCHARD_BLOCK, _collect_stats, _setup_claude_md


def _render_block() -> str:
    return _ORCHARD_BLOCK.format(
        project_name="Demo",
        symbol_count=1,
        calls_count=2,
        contains_count=3,
    )


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


def test_collect_stats_treats_schema_less_database_as_missing(tmp_path):
    project_dir = tmp_path / "project"
    db_path = tmp_path / ".orchard" / "graph.db"
    project_dir.mkdir()
    conn = get_connection(str(db_path))
    conn.close()

    assert _collect_stats(project_dir) == {}


def test_setup_claude_md_reports_missing_database_for_schema_less_database(tmp_path):
    project_dir = tmp_path / "project"
    db_path = tmp_path / ".orchard" / "graph.db"
    project_dir.mkdir()
    conn = get_connection(str(db_path))
    conn.close()

    ok, msg = _setup_claude_md(project_dir)

    assert ok is False
    assert "no orchard database found" in msg
    assert "orchard ingest --project-dir ." in msg
