import os
import pytest
from orchard.build.discovery import discover_index_store_path, discover_symbolgraph_paths
from orchard.build.xcode_settings import (
    discover_compiled_files,
    discover_compiled_targets,
    infer_derived_data_root,
    match_derived_data,
)


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


def test_match_derived_data_accepts_nested_xcodeproj_for_workspace(tmp_path, monkeypatch):
    dd_root = tmp_path / "DerivedData"
    dd_root.mkdir()
    workspace = tmp_path / "ios-client" / "Zoom.xcworkspace"
    workspace.parent.mkdir(parents=True)
    workspace.mkdir()
    nested_project = workspace.parent / "Zoom" / "Zoom.xcodeproj"
    nested_project.parent.mkdir()
    nested_project.mkdir()

    monkeypatch.setattr("orchard.build.xcode_settings.get_derived_data_path", lambda: str(dd_root))

    entry = dd_root / "Zoom-aenx"
    datastore = entry / "Index.noindex" / "DataStore"
    datastore.mkdir(parents=True)
    (entry / "info.plist").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        b'<plist version="1.0"><dict>'
        b'<key>WorkspacePath</key><string>' + str(nested_project).encode() + b'</string>'
        b'<key>LastAccessedDate</key><string>2026-06-29T00:00:00Z</string>'
        b'</dict></plist>'
    )

    candidates = match_derived_data(str(workspace))
    assert candidates[0][0] == str(entry)


def test_infer_derived_data_root_from_index_store_path():
    index_store = (
        "/tmp/DerivedData/MyApp-abc123/Index.noindex/DataStore"
    )

    assert infer_derived_data_root(index_store) == "/tmp/DerivedData/MyApp-abc123"


def test_discover_compiled_targets_reads_build_dirs(tmp_path):
    products = tmp_path / "Index.noindex" / "Build" / "Intermediates.noindex"
    (products / "MyApp.build").mkdir(parents=True)
    (products / "MyFramework.build").mkdir()
    (products / "Debug-iphonesimulator").mkdir()

    targets = discover_compiled_targets(str(tmp_path))

    assert targets == ["MyApp", "MyFramework"]


def test_discover_compiled_files_collects_sources_for_selected_targets(tmp_path):
    products = tmp_path / "Index.noindex" / "Build" / "Intermediates.noindex"
    app_build = products / "MyApp.build"
    framework_build = products / "MyFramework.build"
    other_build = products / "Other.build"
    app_build.mkdir(parents=True)
    framework_build.mkdir()
    other_build.mkdir()

    app_source = tmp_path / "Sources" / "AppDelegate.swift"
    framework_source = tmp_path / "Sources" / "Feature.swift"
    ignored_source = tmp_path / "Sources" / "Ignored.swift"
    for path in (app_source, framework_source, ignored_source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// source\n")

    (app_build / "Objects-normal" / "arm64").mkdir(parents=True)
    (framework_build / "Objects-normal" / "arm64").mkdir(parents=True)
    (other_build / "Objects-normal" / "arm64").mkdir(parents=True)

    (app_build / "Objects-normal" / "arm64" / "AppDelegate.d").write_text(
        f"AppDelegate.o: {app_source}\n"
    )
    (framework_build / "Objects-normal" / "arm64" / "Feature.d").write_text(
        f"Feature.o: {framework_source}\n"
    )
    (other_build / "Objects-normal" / "arm64" / "Ignored.d").write_text(
        f"Ignored.o: {ignored_source}\n"
    )

    files = discover_compiled_files(str(tmp_path), ["MyApp", "MyFramework"])

    assert files == [str(app_source), str(framework_source)]


def test_discover_compiled_files_preserves_escaped_spaces_in_dependency_paths(tmp_path):
    products = tmp_path / "Index.noindex" / "Build" / "Intermediates.noindex"
    app_build = products / "MyApp.build" / "Objects-normal" / "arm64"
    app_build.mkdir(parents=True)

    source_dir = tmp_path / "Sources With Spaces"
    source_dir.mkdir()
    source_path = source_dir / "App Delegate.swift"
    source_path.write_text("// source\n")

    escaped_source = str(source_path).replace(" ", "\\ ")
    (app_build / "AppDelegate.d").write_text(f"AppDelegate.o: {escaped_source}\n")

    files = discover_compiled_files(str(tmp_path), ["MyApp"])

    assert files == [str(source_path)]


def test_discover_compiled_files_skips_malformed_dependency_content(tmp_path):
    products = tmp_path / "Index.noindex" / "Build" / "Intermediates.noindex"
    app_build = products / "MyApp.build" / "Objects-normal" / "arm64"
    app_build.mkdir(parents=True)

    source_path = tmp_path / "Sources" / "AppDelegate.swift"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("// source\n")

    (app_build / "Bad.d").write_bytes(b"\xff\xfe\x00")
    (app_build / "Good.d").write_text(f"AppDelegate.o: {source_path}\n")

    files = discover_compiled_files(str(tmp_path), ["MyApp"])

    assert files == [str(source_path)]


def test_compiled_discovery_ignores_build_dirs_outside_intermediates_root(tmp_path):
    intermediates = tmp_path / "Index.noindex" / "Build" / "Intermediates.noindex"
    target_build = intermediates / "MyApp.build" / "Objects-normal" / "arm64"
    target_build.mkdir(parents=True)

    unrelated_build = tmp_path / "SourcePackages" / "checkouts" / "Pkg.build" / "Objects-normal" / "arm64"
    unrelated_build.mkdir(parents=True)

    app_source = tmp_path / "Sources" / "AppDelegate.swift"
    pkg_source = tmp_path / "Sources" / "PackageFile.swift"
    app_source.parent.mkdir(parents=True)
    app_source.write_text("// app\n")
    pkg_source.write_text("// package\n")

    (target_build / "AppDelegate.d").write_text(f"AppDelegate.o: {app_source}\n")
    (unrelated_build / "PackageFile.d").write_text(f"PackageFile.o: {pkg_source}\n")

    assert discover_compiled_targets(str(tmp_path)) == ["MyApp"]
    assert discover_compiled_files(str(tmp_path), ["MyApp", "Pkg"]) == [str(app_source)]


def test_compiled_discovery_uses_top_level_build_dirs_even_with_child_targets(tmp_path):
    intermediates = tmp_path / "Index.noindex" / "Build" / "Intermediates.noindex"
    container_build = intermediates / "ProjectContainer.build"
    app_build = container_build / "MyApp.build" / "Objects-normal" / "arm64"
    framework_build = container_build / "MyFramework.build" / "Objects-normal" / "arm64"
    app_build.mkdir(parents=True)
    framework_build.mkdir(parents=True)

    app_source = tmp_path / "Sources" / "AppDelegate.swift"
    framework_source = tmp_path / "Sources" / "Feature.swift"
    container_source = tmp_path / "Sources" / "Container.swift"
    app_source.parent.mkdir(parents=True)
    app_source.write_text("// app\n")
    framework_source.write_text("// framework\n")
    container_source.write_text("// container\n")

    (container_build / "Container.d").write_text(f"Container.o: {container_source}\n")
    (app_build / "AppDelegate.d").write_text(f"AppDelegate.o: {app_source}\n")
    (framework_build / "Feature.d").write_text(f"Feature.o: {framework_source}\n")

    assert discover_compiled_targets(str(tmp_path)) == ["ProjectContainer"]
    assert discover_compiled_files(str(tmp_path), ["ProjectContainer"]) == [
        str(app_source),
        str(container_source),
        str(framework_source),
    ]
