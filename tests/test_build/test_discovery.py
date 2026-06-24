import os
import pytest
from orchard.build.discovery import discover_index_store_path, discover_symbolgraph_paths


def test_discover_index_store_path_finds_store(tmp_path):
    store = tmp_path / "Build" / "Intermediates.noindex" / "IndexStore"
    store.mkdir(parents=True)
    result = discover_index_store_path(str(tmp_path))
    assert result == str(store)


def test_discover_index_store_path_returns_none_when_absent(tmp_path):
    result = discover_index_store_path(str(tmp_path))
    assert result is None


def test_discover_symbolgraph_paths_finds_json(tmp_path):
    sg_dir = tmp_path / "Build" / "Products" / "Debug" / "MyApp.build"
    sg_dir.mkdir(parents=True)
    (sg_dir / "MyApp.symbols.json").write_text("{}")
    paths = discover_symbolgraph_paths(str(tmp_path))
    assert any("MyApp.symbols.json" in p for p in paths)


def test_discover_symbolgraph_paths_empty_when_none(tmp_path):
    assert discover_symbolgraph_paths(str(tmp_path)) == []
