from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "orchard" / "SKILL.md"


def test_orchard_skill_describes_single_frame_graph_enrichment():
    text = SKILL.read_text()

    assert "orchard_lookup_frame" in text
    assert "single stack frame" in text
    assert "full crashlogs are handled outside Orchard" in text
    assert "explicit symbol identity" in text


def test_orchard_skill_excludes_crash_analyzer_language():
    text = SKILL.read_text().lower()

    forbidden = [
        "orchard_lookup_crash_thread",
        "crashed-thread triage",
        "crashed thread",
        "arm64 register clues",
        "x0 = 0",
        "arm64_null_this",
        "likely_fault",
        "root_cause",
        "delegate selector inference",
        "business_first_frame",
        "thread_boundaries",
        "dispatch_boundaries",
    ]
    for phrase in forbidden:
        assert phrase not in text
