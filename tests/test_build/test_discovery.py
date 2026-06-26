import os
import pytest
from orchard.build.discovery import discover_index_store_path, discover_symbolgraph_paths
from orchard.build.xcode_settings import match_derived_data


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


def test_match_derived_data_prefers_larger_datastore_when_access_times_tie(tmp_path, monkeypatch):
    dd_root = tmp_path / "DerivedData"
    dd_root.mkdir()
    project = tmp_path / "Zoom.xcodeproj"
    project.mkdir()

    monkeypatch.setattr("orchard.build.xcode_settings.get_derived_data_path", lambda: str(dd_root))

    small = dd_root / "Zoom-small"
    big = dd_root / "Zoom-big"
    for entry, size in ((small, 8), (big, 64)):
        datastore = entry / "Index.noindex" / "DataStore"
        datastore.mkdir(parents=True)
        (entry / "info.plist").write_bytes(
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
            b'<plist version="1.0"><dict>'
            b'<key>WorkspacePath</key><string>' + str(project).encode() + b'</string>'
            b'<key>LastAccessedDate</key><string>2026-06-26T00:00:00Z</string>'
            b'</dict></plist>'
        )
        (datastore / "data").write_bytes(b"x" * size)

    candidates = match_derived_data(str(project))
    assert candidates[0][0] == str(big)
