import json
import pytest
from pathlib import Path
from orchard.ingest.symbolgraph import parse_symbolgraph

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_symbols.json"

def test_parse_symbolgraph_symbols(tmp_path):
    result = parse_symbolgraph(str(FIXTURE), target_id="MyTarget")
    assert len(result.symbols) == 2
    func = next(s for s in result.symbols if s.name == "MyFunc()")
    assert func.usr == "s:8MyModule6MyFuncyyF"
    assert func.language == "swift"
    assert func.module == "MyModule"
    assert func.kind == "swift.func"

def test_parse_symbolgraph_relationships(tmp_path):
    result = parse_symbolgraph(str(FIXTURE), target_id="MyTarget")
    assert len(result.relationships) == 1
    rel = result.relationships[0]
    assert rel.source_usr == "s:8MyModule6MyFuncyyF"
    assert rel.target_usr == "s:8MyModule7MyClassC"
    assert rel.rel_kind == "memberOf"

def test_parse_symbolgraph_access_level():
    result = parse_symbolgraph(str(FIXTURE), target_id="MyTarget")
    cls = next(s for s in result.symbols if s.name == "MyClass")
    assert cls.access_level == "public"
