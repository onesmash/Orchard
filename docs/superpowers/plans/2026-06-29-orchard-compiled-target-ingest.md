# Orchard Compiled-Target Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `orchard ingest` index the targets actually compiled in the current Xcode build by deriving scope from `Intermediates.noindex` and filtering IndexStore records by compiled files instead of `source-root`.

**Architecture:** Keep `IndexStore` as the symbol/relationship source, but derive ingest scope from the matching `DerivedData` root. Add build-discovery helpers that resolve compiled targets and compiled source files from `Intermediates.noindex`, then feed that scope into `cmd_ingest` and `read_index_store(...)` so multi-target ingest is automatic and state/fast-path behavior follows the compiled target set.

**Tech Stack:** Python 3.12, Orchard CLI, Ladybug graph DB, pytest

## Global Constraints

- Remove `--source-root` from `orchard ingest` CLI parsing and behavior.
- Do not add any new user-facing CLI flags for this feature.
- Treat `Intermediates.noindex` as the default Xcode build-scope authority.
- Keep `IndexStore` as the source of symbols, occurrences, and relationships.
- Only promote compiled project targets to ingest targets; do not promote SDK / `pcm` / framework internals.
- Preserve the existing multi-target state merge and placeholder reuse fixes.
- Keep TDD strict: each task starts red, goes green with minimal code, then commits.

---

### Task 1: Add compiled-target and compiled-file discovery helpers

**Files:**
- Modify: `src/orchard/build/xcode_settings.py`
- Modify: `tests/test_build/test_discovery.py`

**Interfaces:**
- Consumes: `match_derived_data(project_path: str) -> list[tuple[str, str, str]]`
- Produces:
  - `infer_derived_data_root(index_store_path: str) -> str | None`
  - `discover_compiled_targets(derived_data_root: str) -> list[str]`
  - `discover_compiled_files(derived_data_root: str, targets: list[str]) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
def test_infer_derived_data_root_from_index_store_path(tmp_path):
    dd = tmp_path / "Zoom-abc"
    store = dd / "Index.noindex" / "DataStore"
    store.mkdir(parents=True)

    from orchard.build.xcode_settings import infer_derived_data_root

    assert infer_derived_data_root(str(store)) == str(dd)


def test_discover_compiled_targets_reads_build_dirs(tmp_path):
    dd = tmp_path / "Zoom-abc"
    inter = dd / "Build" / "Intermediates.noindex"
    (inter / "Zoom.build").mkdir(parents=True)
    (inter / "zPSApp.build").mkdir()
    (inter / "Debug-iphonesimulator").mkdir()

    from orchard.build.xcode_settings import discover_compiled_targets

    assert discover_compiled_targets(str(dd)) == ["Zoom", "zPSApp"]


def test_discover_compiled_files_collects_sources_for_selected_targets(tmp_path):
    dd = tmp_path / "Zoom-abc"
    inter = dd / "Build" / "Intermediates.noindex"
    zoom = inter / "Zoom.build" / "Objects-normal" / "arm64"
    zps = inter / "zPSApp.build" / "Objects-normal" / "arm64"
    zoom.mkdir(parents=True)
    zps.mkdir(parents=True)
    (zoom / "Zoom.d").write_text("/repo/ios-client/Zoom/AppDelegate.m \\\n/repo/ios-client/Zoom/ViewController.m\n")
    (zps / "CPSContext.d").write_text("/repo/client-app-video/zPSApp/src/App/Context/CPSContext.cpp\n")

    from orchard.build.xcode_settings import discover_compiled_files

    assert sorted(discover_compiled_files(str(dd), ["Zoom", "zPSApp"])) == [
        "/repo/client-app-video/zPSApp/src/App/Context/CPSContext.cpp",
        "/repo/ios-client/Zoom/AppDelegate.m",
        "/repo/ios-client/Zoom/ViewController.m",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_build/test_discovery.py -k "derived_data_root or discover_compiled_targets or discover_compiled_files"`

Expected: FAIL with import or attribute errors for the new helper functions.

- [ ] **Step 3: Write minimal implementation**

```python
def infer_derived_data_root(index_store_path: str) -> str | None:
    p = Path(index_store_path).resolve()
    parts = p.parts
    try:
        idx = parts.index("Index.noindex")
    except ValueError:
        return None
    return str(Path(*parts[:idx]))


def discover_compiled_targets(derived_data_root: str) -> list[str]:
    inter = Path(derived_data_root) / "Build" / "Intermediates.noindex"
    if not inter.is_dir():
        return []
    names = []
    for entry in inter.iterdir():
        if entry.is_dir() and entry.name.endswith(".build"):
            names.append(entry.name[:-6])
    return sorted(dict.fromkeys(names))


def discover_compiled_files(derived_data_root: str, targets: list[str]) -> list[str]:
    inter = Path(derived_data_root) / "Build" / "Intermediates.noindex"
    files: set[str] = set()
    for target in targets:
        for depfile in inter.rglob(f"{target}.build/**/*.d"):
            for line in depfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                normalized = line.replace("\\", " ").strip()
                for token in normalized.split():
                    if token.startswith("/") and "." in Path(token).name:
                        files.add(token)
    return sorted(files)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_build/test_discovery.py -k "derived_data_root or discover_compiled_targets or discover_compiled_files"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/build/xcode_settings.py tests/test_build/test_discovery.py
git commit -m "feat: discover compiled targets from DerivedData"
```

### Task 2: Replace `source-root` filtering with compiled-file filtering in IndexStore ingest

**Files:**
- Modify: `src/orchard/ingest/indexstore.py`
- Modify: `tests/test_ingest/test_indexstore_real_cli.py`
- Modify: `tests/test_acceptance.py`

**Interfaces:**
- Consumes:
  - `discover_compiled_files(derived_data_root: str, targets: list[str]) -> list[str]`
  - `read_index_store(index_store_path: str, target_id: str, incremental_since: float | None = None)`
- Produces:
  - `read_index_store(..., allowed_files: set[str] | None = None) -> tuple[IndexStoreResult, dict | None]`
  - filtering semantics based on exact compiled-file membership

- [ ] **Step 1: Write the failing tests**

```python
def test_read_index_store_filters_by_allowed_files(monkeypatch):
    from orchard.ingest.indexstore import read_index_store

    def fake_run_cli(*_args, **_kwargs):
        lines = [
            '{"kind":"symbol","usr":"s:zoom","name":"Zoom","symbol_kind":"function","language":"objc","module":"Zoom","file":"/repo/ios-client/Zoom/AppDelegate.m"}',
            '{"kind":"symbol","usr":"s:zps","name":"CPSContext","symbol_kind":"function","language":"cxx","module":"zPSApp","file":"/repo/client-app-video/zPSApp/src/App/Context/CPSContext.cpp"}',
        ]
        return lines, ""

    monkeypatch.setattr("orchard.ingest.indexstore._run_cli", fake_run_cli)

    result, _ = read_index_store(
        "/fake/store",
        target_id="Zoom",
        allowed_files={"/repo/client-app-video/zPSApp/src/App/Context/CPSContext.cpp"},
    )

    assert [s.usr for s in result.symbols] == ["s:zps"]


def test_cmd_ingest_uses_compiled_targets_from_derived_data(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    captured = {}

    class DummyConn:
        def close(self):
            return None

    def fake_read_index_store(index_store_path, target_id, incremental_since=None, allowed_files=None):
        captured["target_id"] = target_id
        captured["allowed_files"] = allowed_files
        return IndexStoreResult(), None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(tmp_path / "Zoom.xcodeproj"))
    monkeypatch.setattr("orchard.build.xcode_settings.match_derived_data", lambda _: [(str(tmp_path / "Zoom-abc"), str(tmp_path / "Zoom-abc/Index.noindex/DataStore"), "2026-06-29T00:00:00Z")])
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_files", lambda *_args: ["/repo/ios-client/Zoom/AppDelegate.m", "/repo/client-app-video/zPSApp/src/App/Context/CPSContext.cpp"])
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", fake_read_index_store)
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)

    cmd_ingest(["--project-dir", str(tmp_path), "--target", "Zoom"])

    assert captured["target_id"] == "Zoom"
    assert captured["allowed_files"] == {
        "/repo/ios-client/Zoom/AppDelegate.m",
        "/repo/client-app-video/zPSApp/src/App/Context/CPSContext.cpp",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_acceptance.py -k "compiled_targets_from_derived_data" tests/test_ingest/test_indexstore_real_cli.py -k "allowed_files"`

Expected: FAIL because `read_index_store` does not accept `allowed_files` and `cmd_ingest` still passes `source_root`.

- [ ] **Step 3: Write minimal implementation**

```python
def read_index_store(
    index_store_path: str,
    target_id: str,
    incremental_since: float | None = None,
    allowed_files: set[str] | None = None,
) -> tuple[IndexStoreResult, dict | None]:
    ...
    for raw in lines:
        ...
        file_path = obj.get("file", "")
        if allowed_files is not None and file_path and file_path not in allowed_files:
            continue
```

```python
compiled_targets = discover_compiled_targets(dd_dir)
if ns.target and ns.target not in compiled_targets:
    print(f"error: target '{ns.target}' was not compiled in DerivedData '{dd_dir}'.", file=sys.stderr)
    print(f"  compiled targets: {', '.join(compiled_targets)}", file=sys.stderr)
    sys.exit(2)
targets = compiled_targets
allowed_files = set(discover_compiled_files(dd_dir, targets))
r, file_status = read_index_store(
    index_store,
    ns.target or targets[0],
    incremental_since=incremental_since,
    allowed_files=allowed_files,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_acceptance.py -k "compiled_targets_from_derived_data" tests/test_ingest/test_indexstore_real_cli.py -k "allowed_files"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/ingest/indexstore.py src/orchard/cli.py tests/test_acceptance.py tests/test_ingest/test_indexstore_real_cli.py
git commit -m "feat: ingest compiled targets from IndexStore build scope"
```

### Task 3: Remove `--source-root`, harden errors, and align state / fast-path behavior

**Files:**
- Modify: `src/orchard/cli.py`
- Modify: `src/orchard/ingest/state.py`
- Modify: `tests/test_acceptance.py`

**Interfaces:**
- Consumes:
  - `discover_compiled_targets(derived_data_root: str) -> list[str]`
  - `infer_derived_data_root(index_store_path: str) -> str | None`
- Produces:
  - `orchard ingest` CLI without `--source-root`
  - state entries keyed by compiled target set
  - fast-path guard keyed by compiled target set

- [ ] **Step 1: Write the failing tests**

```python
def test_cmd_ingest_rejects_unknown_target_for_compiled_scope(tmp_path, monkeypatch, capsys):
    from orchard.cli import cmd_ingest

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(tmp_path / "Zoom.xcodeproj"))
    monkeypatch.setattr("orchard.build.xcode_settings.match_derived_data", lambda _: [(str(tmp_path / "Zoom-abc"), str(tmp_path / "Zoom-abc/Index.noindex/DataStore"), "2026-06-29T00:00:00Z")])
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["zPSApp"])

    with pytest.raises(SystemExit):
        cmd_ingest(["--project-dir", str(tmp_path), "--target", "Zoom"])

    err = capsys.readouterr().err
    assert "compiled targets" in err
    assert "zPSApp" in err


def test_cmd_ingest_stores_compiled_targets_in_state(tmp_path, monkeypatch):
    from orchard.cli import cmd_ingest
    from orchard.ingest.indexstore import IndexStoreResult

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr("orchard.cli._conn", lambda *_args, **_kwargs: DummyConn())
    monkeypatch.setattr("orchard.build.xcode_settings.find_xcode_project", lambda _: str(tmp_path / "Zoom.xcodeproj"))
    monkeypatch.setattr("orchard.build.xcode_settings.match_derived_data", lambda _: [(str(tmp_path / "Zoom-abc"), str(tmp_path / "Zoom-abc/Index.noindex/DataStore"), "2026-06-29T00:00:00Z")])
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_targets", lambda _: ["Zoom", "zPSApp"])
    monkeypatch.setattr("orchard.build.xcode_settings.discover_compiled_files", lambda *_args: ["/repo/ios-client/Zoom/AppDelegate.m"])
    monkeypatch.setattr("orchard.ingest.indexstore.read_index_store", lambda *args, **kwargs: (IndexStoreResult(), None))
    monkeypatch.setattr("orchard.normalize.identity.upsert_symbols", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_calls", lambda *args, **kwargs: 0)
    monkeypatch.setattr("orchard.normalize.identity.upsert_indexstore_rels", lambda *args, **kwargs: 0)

    cmd_ingest(["--project-dir", str(tmp_path), "--target", "Zoom"])

    data = json.loads((tmp_path / ".orchard" / "ingest-state.json").read_text())
    assert data["targets"] == ["Zoom", "zPSApp"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_acceptance.py -k "unknown_target_for_compiled_scope or stores_compiled_targets_in_state"`

Expected: FAIL because `cmd_ingest` still accepts `--source-root` semantics and does not validate or persist compiled target scope.

- [ ] **Step 3: Write minimal implementation**

```python
ap = argparse.ArgumentParser(prog="orchard ingest")
ap.add_argument("--index-store", default="")
ap.add_argument("--project-dir", default=os.getcwd())
ap.add_argument("--target", default="")
ap.add_argument("--db", default="")
```

```python
derived_data_root = dd_dir if not ns.index_store else infer_derived_data_root(index_store)
if derived_data_root is None:
    print("error: could not derive DerivedData root from the supplied --index-store path.", file=sys.stderr)
    sys.exit(2)
compiled_targets = discover_compiled_targets(derived_data_root)
if not compiled_targets:
    print("error: no compiled targets discovered under Intermediates.noindex.", file=sys.stderr)
    sys.exit(2)
```

```python
requested_targets = set(compiled_targets)
prev_targets = set(old_state.get("targets", []) if old_state else [])
if unit_ts <= incremental_since and requested_targets.issubset(prev_targets):
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_acceptance.py -k "unknown_target_for_compiled_scope or stores_compiled_targets_in_state"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchard/cli.py src/orchard/ingest/state.py tests/test_acceptance.py
git commit -m "refactor: make compiled targets the default ingest scope"
```

### Task 4: Run the integrated regression suite and update docs

**Files:**
- Modify: `docs/superpowers/specs/2026-06-29-orchard-compiled-target-ingest-design.md`
- Modify: `AGENTS.md`
- Modify: `src/orchard/cli.py` (help text only if needed)

**Interfaces:**
- Consumes: completed implementation from Tasks 1-3
- Produces: updated user-facing documentation and a verified regression pass

- [ ] **Step 1: Write the failing doc/behavior check**

```python
def test_cmd_ingest_help_no_longer_mentions_source_root(capsys):
    from orchard.cli import cmd_ingest

    with pytest.raises(SystemExit):
        cmd_ingest(["--help"])

    out = capsys.readouterr().out
    assert "--source-root" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_acceptance.py -k "no_longer_mentions_source_root"`

Expected: FAIL because CLI help still advertises `--source-root`.

- [ ] **Step 3: Write minimal implementation**

```markdown
- Update Orchard ingest docs to describe compiled-target default behavior.
- Remove `--source-root` from CLI help text and usage examples.
- Add one concise note explaining that compiled targets are derived from `Intermediates.noindex`.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_acceptance.py -k "no_longer_mentions_source_root" && uv run pytest -q tests/test_build/test_discovery.py tests/test_normalize/test_identity.py tests/test_acceptance.py tests/test_pipeline/test_runner.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/superpowers/specs/2026-06-29-orchard-compiled-target-ingest-design.md src/orchard/cli.py tests/test_acceptance.py
git commit -m "docs: describe compiled-target ingest defaults"
```

## Self-Review

- Spec coverage:
  - compiled-target discovery: Task 1
  - replace `source-root` with compiled-file filtering: Task 2
  - manual `--index-store` DerivedData resolution: Tasks 1 and 3
  - state and fast-path behavior tied to compiled target set: Task 3
  - errors and migration/docs: Tasks 3 and 4
- Placeholder scan:
  - no `TBD` / `TODO` / "implement later" placeholders remain
  - all code-changing steps include concrete code snippets
- Type consistency:
  - helper names are consistent across tasks: `infer_derived_data_root`, `discover_compiled_targets`, `discover_compiled_files`
  - `read_index_store(..., allowed_files=...)` naming is used consistently in plan steps

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-29-orchard-compiled-target-ingest.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
