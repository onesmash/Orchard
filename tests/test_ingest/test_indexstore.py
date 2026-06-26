import json
import pytest
from unittest.mock import patch
from orchard.ingest.indexstore import read_index_store, OccurrenceRecord, RelationRecord

_SAMPLE_LINES = [
    json.dumps({"kind": "occurrence", "usr": "s:MyFunc", "file": "/src/f.swift",
                "line": 10, "column": 5, "role": "definition"}),
    json.dumps({"kind": "relation", "from_usr": "s:MyFunc", "to_usr": "s:OtherFunc",
                "role": "calledBy", "occurrence_role": "call",
                "file": "/src/f.swift", "line": 10, "column": 12}),
]

def _mock_cli(lines):
    """Return a generator that yields lines (matches streaming _run_cli)."""
    for line in lines:
        yield line

def test_read_index_store_parses_occurrences():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert len(result.occurrences) == 1
    occ = result.occurrences[0]
    assert occ.usr == "s:MyFunc"
    assert occ.file_path == "/src/f.swift"
    assert occ.line == 10
    assert occ.col == 5
    assert occ.role == "definition"

def test_read_index_store_parses_relations():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(_SAMPLE_LINES)):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert len(result.relations) == 1
    rel = result.relations[0]
    assert rel.from_usr == "s:MyFunc"
    assert rel.to_usr == "s:OtherFunc"
    assert rel.role == "calledBy"
    assert rel.occurrence_role == "call"
    assert rel.file_path == "/src/f.swift"
    assert rel.line == 10
    assert rel.col == 12

def test_read_index_store_empty_store():
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli([])):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert result.occurrences == []
    assert result.relations == []


def test_read_index_store_tolerates_malformed_lines():
    lines = [
        '{"kind":"occurrence","usr":"s:A","file":"f.swift","line":1,"column":1,"role":"definition"}',
        'NOT VALID JSON',
    ]
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(lines)):
        result = read_index_store("/fake/store", target_id="T")
    assert len(result.occurrences) == 1
    assert len(result.warnings) == 1
    assert "NOT VALID" in result.warnings[0]


def test_read_index_store_tolerates_missing_keys():
    lines = [
        '{"kind":"occurrence","usr":"s:A","file":"f.swift","line":1,"column":1}',
        '{"kind":"relation","from_usr":"a","to_usr":"b"}',
    ]
    with patch("orchard.ingest.indexstore._run_cli", side_effect=lambda *a, **kw: _mock_cli(lines)):
        result = read_index_store("/fake/store", target_id="T")
    assert len(result.occurrences) == 0
    assert len(result.relations) == 0
    assert len(result.warnings) == 2
