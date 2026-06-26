# Orchard Wiring Completion (Revised)

> 接线前两轮已实现但未集成的组件。根据 dev+test review 修订。

## 接线任务（4 项，从 6 项缩减）

### 1. subtype closure 接入 impact (`src/orchard/handlers/impact.py`) ✅ 明确
- `_subtype_closure()` 接入 `impact_analysis()`
- **修正（dev review）**：closure 结果**先 seed 进 visited_ids**再 BFS，避免 conformer 在 BFS 和 closure 中重复计数
- closure USR 查符号元数据后并入 d1，标记 `reached_via="subtype_closure"`
- 更新 risk_level 计算

### 2. freshness 过滤接入 impact (`src/orchard/handlers/impact.py`) ✅ 明确
- **修正（dev review）**：Symbol 无 timestamp 字段 → 用 `BuildSnapshot.created_at` 作为索引时间戳
- **修正（test review）**：`file_path=""` 时跳过 mtime 检查（默认 up-to-date），避免 os.path.getmtime 抛错
- impact BFS 遍历的 dependent 若 file 已删除/修改则过滤

### 3. CrossLanguageName 填充 (`src/orchard/derive/bridge.py`) ✅ 明确
- **修正（dev+test review）**：BridgesTo 缺列 → 先加 schema 迁移（clang_name/swift_name/definition_language）
- ObjC 选择器格式化：实例方法 `-[Cls method:]`，类方法 `+[Cls method:]`
- Swift 名：`Cls.method(_:)`
- `run_bridge_recovery` 写入双语言名；handlers/bridges.py 返回

### 4. 补全测试
- subtype closure 接线（impact 含 subtypes）
- freshness 接线（过期/空路径过滤）
- 社区检测（之前 0 覆盖）
- 流程检测（之前 0 覆盖）
- CrossLanguageName 填充

## 缩减项（根据 review）

### ❌ Task 3（pipeline runner 全量重写）— DESCOPED
- dev review：近乎全量重写，丢失 asyncio.gather 并行性，abstraction mismatch
- **改为**：仅把 MRO/社区/流程注册为**可独立调用的 derive 函数**（已是），不改主 runner
- 主 runner 保持现状，新阶段通过 CLI/手动触发

### ❌ Task 5（hybrid search 接入 semantic_search）— DESCOPED
- dev review：semantic_search.py **已是混合搜索**（vector cosine + FTS deduped）
- `hybrid_search()` helper 是降级（vector pass 忽略 embedding）
- **改为**：保留 rrf_fuse 作为独立工具，不替换现有 handler

## 实现顺序
1. → subtype closure 接线（impact.py）
2. → freshness 接线（impact.py，同文件）
3. → BridgesTo schema 迁移 + CrossLanguageName 填充
4. → 补全测试

## 文件变更
| 文件 | 改动 |
|------|------|
| `src/orchard/handlers/impact.py` | subtype + freshness 接线 |
| `src/orchard/graph/schema.py` | BridgesTo 加 3 列 |
| `src/orchard/derive/bridge.py` | CrossLanguageName 填充 |
| `src/orchard/handlers/bridges.py` | 返回 cross-language 名 |
| 多个 test 文件 | 补全测试 |
