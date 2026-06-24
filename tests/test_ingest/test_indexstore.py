import json
import pytest
from unittest.mock import patch
from orchard.ingest.indexstore import read_index_store, OccurrenceRecord, RelationRecord

SAMPLE_OUTPUT = "\n".join([
    json.dumps({"kind": "occurrence", "usr": "s:MyFunc", "file": "/src/f.swift",
                "line": 10, "column": 5, "role": "definition"}),
    json.dumps({"kind": "relation", "from_usr": "s:MyFunc", "to_usr": "s:OtherFunc",
                "role": "call"}),
])

def test_read_index_store_parses_occurrences():
    with patch("orchard.ingest.indexstore._run_cli", return_value=SAMPLE_OUTPUT):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert len(result.occurrences) == 1
    occ = result.occurrences[0]
    assert occ.usr == "s:MyFunc"
    assert occ.file_path == "/src/f.swift"
    assert occ.line == 10
    assert occ.role == "definition"

def test_read_index_store_parses_relations():
    with patch("orchard.ingest.indexstore._run_cli", return_value=SAMPLE_OUTPUT):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert len(result.relations) == 1
    rel = result.relations[0]
    assert rel.from_usr == "s:MyFunc"
    assert rel.to_usr == "s:OtherFunc"
    assert rel.role == "call"

def test_read_index_store_empty_store():
    with patch("orchard.ingest.indexstore._run_cli", return_value=""):
        result = read_index_store("/fake/store", target_id="MyTarget")
    assert result.occurrences == []
    assert result.relations == []
