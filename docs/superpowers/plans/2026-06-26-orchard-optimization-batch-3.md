# Orchard Optimization Batch 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Orchard 当前最关键的数据正确性、CLI 可解释性和 freshness 默认行为问题，并为 Phase 2 的旧 USR 清理与 bridge 深化打下可验证基础。

**Architecture:** 本轮先集中修复 ingest 和 CLI/query 的接线层，让双来源符号在进入 `upsert_symbols()` 之前完成字段级合并，并让 CLI 默认绑定最新 BuildSnapshot、显式展示 DB/build 上下文。所有行为优先通过集成测试固定契约，再做最小实现，避免把查询层和 schema 演化揉进一次大重构。

**Tech Stack:** Python 3, Orchard CLI/handlers/query layers, Ladybug graph queries, pytest

## Global Constraints

- 执行模式固定记录为 `subagent-driven`，不向用户提供执行模式选择题。
- 本阶段不做无关重构，优先修复 handoff 对应问题闭环。
- 修改函数、类、方法前必须先做 GitNexus `impact` 上游影响分析。
- 手工编辑必须使用 `apply_patch`。
- 测试优先覆盖 integration 行为：ingest 合并、CLI 输出、freshness 默认 build、callers 响应 shape。

---

## File Structure

- Modify: `src/orchard/pipeline/runner.py`
  - 在 ingest 主流程里接入 SymbolGraph / IndexStore 字段级合并。
- Modify: `src/orchard/normalize/identity.py`
  - 为后续 update / prune 预留最小辅助函数，避免把 merge 逻辑塞进 bulk upsert 内部。
- Modify: `src/orchard/cli.py`
  - 修复默认 DB 路径、父目录 DB 提示、latest build 默认绑定、stats 元信息输出、source-root 诊断。
- Modify: `src/orchard/query/lookup.py`
  - 扩展 callers 查询，返回 `file_path`, `line`, `col`，并提供 latest build helper 所需最小查询。
- Modify: `src/orchard/handlers/callers.py`
  - 暴露 callers 新字段并保留兼容响应。
- Modify: `src/orchard/build/xcode_settings.py`
  - 改进 DerivedData 候选排序，增加 size 权重或等价的更合理排序信息。
- Modify: `src/orchard/validation/freshness.py`
  - 如需要，补充 latest snapshot 读取辅助逻辑。
- Modify: `src/orchard/graph/schema.py`
  - 若采用 additive schema，加入 `swift_display_name`。
- Modify: `src/orchard/derive/bridge.py`
  - 如主线实现需要，消费 `swift_display_name` 或保持兼容桥接输出。
- Test: `tests/test_pipeline/test_runner.py`
  - 覆盖 merge 后 ingest 行为。
- Test: `tests/test_query/` and/or `tests/test_handlers/`
  - 覆盖 callers 新字段。
- Test: `tests/test_validation/`
  - 覆盖 freshness 默认 latest build。
- Test: `tests/test_build/` / `tests/test_ingest/` / `tests/test_acceptance*.py`
  - 覆盖 CLI/ingest/source-root/stats 诊断。

### Task 1: 锁定双来源 merge 契约与 schema 增量字段

**Files:**
- Modify: `src/orchard/pipeline/runner.py`
- Modify: `src/orchard/graph/schema.py`
- Test: `tests/test_pipeline/test_runner.py`

**Interfaces:**
- Consumes: `parse_symbolgraph(...)`, `read_index_store(...)`, `upsert_symbols(conn, symbols, target_id)`
- Produces:
  - `_merge_symbol_sources(...) -> list[SymbolRecord]`
  - `Symbol.swift_display_name` persisted when schema 可用

- [ ] **Step 1: 写失败测试，固定 merge precedence**

```python
def test_pipeline_merge_prefers_indexstore_path_and_name(tmp_db_path, monkeypatch):
    # same USR from both sources
    # symbolgraph path/name should lose to indexstore path/name
    # swift_display_name should preserve the SymbolGraph presentation
    ...
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_pipeline/test_runner.py -k merge_prefers_indexstore -v`
Expected: FAIL，当前 pipeline 没有 merge helper，仍走 fallback-only 逻辑

- [ ] **Step 3: 最小实现 merge helper 与 additive schema**

```python
def _merge_symbol_sources(symbolgraph_symbols, indexstore_symbols):
    by_usr = {}
    ...
    merged.name = indexstore_name or symbolgraph_name
    merged.file_path = indexstore_path or symbolgraph_path
    merged.swift_display_name = symbolgraph_name if symbolgraph_name != merged.name else ""
    return list(by_usr.values())
```

- [ ] **Step 4: 运行目标测试确认通过**

Run: `pytest tests/test_pipeline/test_runner.py -k merge_prefers_indexstore -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/orchard/pipeline/runner.py src/orchard/graph/schema.py tests/test_pipeline/test_runner.py
git commit -m "feat: merge symbolgraph and indexstore symbol fields"
```

### Task 2: callers 查询补全位置字段并保持兼容

**Files:**
- Modify: `src/orchard/query/lookup.py`
- Modify: `src/orchard/handlers/callers.py`
- Test: `tests/test_handlers/test_callers.py`

**Interfaces:**
- Consumes: `GraphLookup.callers_of(usr, target_id)`
- Produces:
  - callers item fields: `file_path: str`, `line: int | None`, `col: int | None`

- [ ] **Step 1: 写失败测试，锁定 callers 输出结构**

```python
def test_find_callers_returns_location_fields(tmp_db_path):
    resp = find_callers(...)
    row = resp.data[0]
    assert "file_path" in row
    assert "line" in row
    assert "col" in row
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_handlers/test_callers.py -v`
Expected: FAIL，当前 callers 结果没有这些字段

- [ ] **Step 3: 最小实现 lookup / handler 扩展**

```python
RETURN DISTINCT caller.usr, caller.name, caller.module,
       caller.kind, caller.language, caller.file_path,
       def.line, def.col
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_handlers/test_callers.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/orchard/query/lookup.py src/orchard/handlers/callers.py tests/test_handlers/test_callers.py
git commit -m "feat: return caller location fields"
```

### Task 3: 修复 CLI 的 DB/build 默认语义与 stats 上下文

**Files:**
- Modify: `src/orchard/cli.py`
- Modify: `src/orchard/validation/freshness.py`
- Test: `tests/test_validation/test_freshness.py`
- Test: `tests/test_acceptance.py`

**Interfaces:**
- Consumes: `_find_project_db()`, `freshness_for(conn, build_id, query_ctx)`
- Produces:
  - `_latest_build_id(conn, target_id) -> str`
  - `stats` 输出 DB 路径、snapshot 元信息、freshness

- [ ] **Step 1: 写失败测试，锁定 latest build 默认绑定**

```python
def test_cli_defaults_to_latest_build_for_freshness(tmp_db_path, capsys):
    ...
    assert payload["freshness"] == "fresh"
```

- [ ] **Step 2: 写失败测试，锁定 stats 元信息输出**

```python
def test_stats_prints_db_path_and_snapshot_metadata(tmp_db_path, capsys):
    cmd_stats(["--db", str(tmp_db_path)])
    out = capsys.readouterr().out
    assert "Database:" in out
    assert "IndexStore:" in out
```

- [ ] **Step 3: 运行测试确认当前失败**

Run: `pytest tests/test_validation/test_freshness.py tests/test_acceptance.py -k "latest_build or stats_prints" -v`
Expected: FAIL，当前 CLI 不会默认取 latest build，`stats` 也不输出这些上下文

- [ ] **Step 4: 最小实现 latest build helper 与 stats 扩展**

```python
def _latest_build_id(conn, target_id):
    ...

def cmd_stats(args):
    print(f"Database: {db}")
    ...
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_validation/test_freshness.py tests/test_acceptance.py -k "latest_build or stats_prints" -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/orchard/cli.py src/orchard/validation/freshness.py tests/test_validation/test_freshness.py tests/test_acceptance.py
git commit -m "feat: improve cli db and freshness defaults"
```

### Task 4: 修复 ingest 路径推断、source-root 标准化与诊断输出

**Files:**
- Modify: `src/orchard/cli.py`
- Modify: `src/orchard/build/xcode_settings.py`
- Test: `tests/test_build/test_discovery.py`
- Test: `tests/test_ingest/test_indexstore.py`

**Interfaces:**
- Consumes: `find_xcode_project(...)`, `match_derived_data(...)`
- Produces:
  - 更合理的 candidate 排序
  - `source_root` 绝对路径标准化
  - 0-symbol warning / parent-db info 输出

- [ ] **Step 1: 写失败测试，锁定 source-root 相对路径标准化**

```python
def test_ingest_resolves_relative_source_root_before_read(monkeypatch, tmp_path):
    ...
```

- [ ] **Step 2: 写失败测试，锁定 parent DB 提示**

```python
def test_find_project_db_reports_parent_db(capsys, monkeypatch, tmp_path):
    ...
```

- [ ] **Step 3: 运行测试确认当前失败**

Run: `pytest tests/test_build/test_discovery.py tests/test_ingest/test_indexstore.py -k "source_root or parent_db" -v`
Expected: FAIL，当前行为仍是静默和未标准化

- [ ] **Step 4: 最小实现路径与诊断增强**

```python
source_root = str(Path(source_root).resolve())
print(f"Using database at {path} (found in parent directory)")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_build/test_discovery.py tests/test_ingest/test_indexstore.py -k "source_root or parent_db" -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/orchard/cli.py src/orchard/build/xcode_settings.py tests/test_build/test_discovery.py tests/test_ingest/test_indexstore.py
git commit -m "feat: improve ingest path resolution and diagnostics"
```

### Task 5: 连续推进旧 USR 清理的最小闭环

**Files:**
- Modify: `src/orchard/normalize/identity.py`
- Modify: `src/orchard/pipeline/runner.py`
- Test: `tests/test_normalize/test_identity.py`

**Interfaces:**
- Consumes: target-scoped current build USR set
- Produces:
  - `prune_missing_symbols(conn, target_id, active_usrs) -> int`

- [ ] **Step 1: 写失败测试，锁定同 target 的旧 USR 会被清理**

```python
def test_prune_missing_symbols_removes_symbols_not_in_current_build(tmp_db_path):
    ...
```

- [ ] **Step 2: 写失败测试，锁定不会误删当前 build 仍存在的符号**

```python
def test_prune_missing_symbols_keeps_active_symbols(tmp_db_path):
    ...
```

- [ ] **Step 3: 运行测试确认当前失败**

Run: `pytest tests/test_normalize/test_identity.py -k prune_missing_symbols -v`
Expected: FAIL，当前没有 prune 逻辑

- [ ] **Step 4: 最小实现 target-scoped prune**

```python
def prune_missing_symbols(conn, target_id, active_usrs):
    ...
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_normalize/test_identity.py -k prune_missing_symbols -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/orchard/normalize/identity.py src/orchard/pipeline/runner.py tests/test_normalize/test_identity.py
git commit -m "feat: prune stale symbols for current target build"
```

## Self-Review

- Spec coverage:
  - `#1/#8` 双来源 merge 与双名保留：Task 1
  - `#4` callers 位置信息：Task 2
  - `#9/#10` stats 与 freshness 默认 build：Task 3
  - `#6/#7` source-root / DB 发现 / ingest 诊断：Task 4
  - `#2` 旧 USR 清理连续推进：Task 5
- Placeholder scan:
  - 无 `TODO/TBD/implement later`
  - 每个任务都给了明确测试入口和代码落点
- Type consistency:
  - `swift_display_name`
  - `_latest_build_id(conn, target_id) -> str`
  - `prune_missing_symbols(conn, target_id, active_usrs) -> int`

## Execution Handoff

本计划记录的执行模式为 `subagent-driven`。
