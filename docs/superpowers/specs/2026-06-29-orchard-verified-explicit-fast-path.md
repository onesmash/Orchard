# Orchard Verified Explicit Fast Path

> 状态：设计确认版。
> 目标：让 `orchard ingest --incremental` 默认优先走高性能 fast path，同时用严格的 `>= baseline` 质量门槛保证“不降质”。
> 约束：不使用任何本地 output-path 推导器；explicit 模式只消费外部可信 provider 或 Orchard 自己产出的 verified manifest。

## 背景

当前 Orchard 在大型 Xcode IndexStore 上存在两个并存问题：

1. 第二次 `orchard ingest --incremental` 仍然偏慢。
2. 直接启用 `useExplicitOutputUnits` 虽然有明显性能收益，但在真实 Zoom 工程上出现过索引质量退化。

本轮设计的核心结论是：

- `useExplicitOutputUnits` 可以作为 fast path 的核心机制。
- 但它不能裸开，必须建立在“可信 output path 集合 + 质量验证 + 自动回退”之上。
- 不再接受 `build.db`、`OutputFileMap`、`.pch/.pcm` 扫描等本地猜测路径逻辑参与决策。

## 借鉴 sourcekit-lsp 的点

`sourcekit-lsp` 值得借鉴的不是单个参数，而是一整套模式：

1. 只有 build system 明确支持 output paths 时，才启用 `useExplicitOutputUnits`。
2. 初始化 `IndexStoreDB` 后，再把 output paths 集合灌进去，而不是要求构造时一次性决定全部内容。
3. 维护的是“当前可见 output paths 集合”，并按 add/remove diff 增量更新。
4. 保留全量扫描作为初次建库、手动修复和兜底路径。

Orchard 采用同样的结构，但把 output path 真相源收缩到两类：

1. 外部可信 provider 直接提供的 canonical output paths。
2. Orchard 自己先前 full scan 或已验证 explicit 运行后持久化的 verified manifest。

## 设计目标

### P0

1. `--incremental` 默认尝试 verified explicit fast path。
2. 任意质量下降都不允许通过；质量门槛固定为 `>= baseline`。
3. fast path 失败时自动回退 full scan，不要求用户干预。
4. 不引入本地 output-path 推导器。

### Non-Goals

1. 不尝试从 `build.db`、`OutputFileMap`、DerivedData 目录扫描中恢复 output paths。
2. 不在本轮引入复杂的文件级/符号级自适应阈值。
3. 不要求第一次 ingest 也必须快；第一次允许 full scan 建立基线。

## 术语

### verified manifest

一份 Orchard 自己持久化的、已经通过质量校验的 explicit output path 清单。

### quality baseline

一组最小质量指标，用于判断 explicit 结果是否“至少不比上一次已验证结果差”。

第一版 baseline 固定为三项：

- `symbols`
- `relations`
- `occurrences`

### fast path

使用 `useExplicitOutputUnits: true` 打开 `IndexStoreDB`，并通过 `addUnitOutFilePaths(...)` 灌入 verified output paths 的执行路径。

### fallback full scan

使用当前稳定的 full scan / `pollForUnitChangesAndWait(isInitialScan: true)` 路径全量导入 IndexStore 的执行路径。

## 决策总览

### 1. explicit fast path 只吃 verified 数据

fast path 的 output paths 只允许来自：

1. 外部 provider 的 canonical output paths。
2. 先前已验证 manifest 中的 `output_paths`。

以下来源全部禁止参与 fast path 决策：

- `build.db`
- `OutputFileMap`
- `.pch`
- `.gch`
- `.pcm`
- 其他 DerivedData 本地扫描推导

### 2. baseline 规则固定为 `>=`

explicit 运行完成后，必须满足：

- `symbols >= baseline.symbols`
- `relations >= baseline.relations`
- `occurrences >= baseline.occurrences`

只要任意一项不满足，就判定为质量退化，立即回退 full scan。

不接受百分比阈值、模糊窗口或“轻微下降可接受”的策略。

### 3. baseline 只允许被已验证结果更新

以下结果可以写入或刷新 baseline：

1. 一次成功完成的 full scan。
2. 一次通过 `>= baseline` 校验的 explicit 结果。

以下结果绝不能写回 baseline：

1. 失败的 explicit 结果。
2. 未经过质量校验的结果。
3. 从可疑 DB 或旧 schema 恢复出的历史值。

### 4. full scan 是系统内建兜底，不是异常路径

首次 ingest、manifest miss、manifest 失效、explicit 失败、质量校验失败时，都允许直接走 full scan。

这不是错误恢复，而是设计的一部分。

## Manifest 设计

### 存储位置

建议放在用户级缓存目录，例如：

- `~/.orchard/explicit-manifests/<manifest-key>.json`

manifest key 应稳定反映“这个 explicit 集合适用于哪个 IndexStore 上下文”。

### 字段

第一版建议字段：

```json
{
  "schema_version": "v1",
  "index_store_path": "...",
  "source_root": "...",
  "selected_index_store": "...",
  "last_ingest_ts": 0,
  "unit_ts": 0,
  "output_paths": ["..."],
  "quality_baseline": {
    "symbols": 0,
    "relations": 0,
    "occurrences": 0
  }
}
```

### 命中条件

manifest 命中必须同时满足：

1. `index_store_path` 一致。
2. `source_root` 一致。
3. `selected_index_store` 一致。
4. `last_ingest_ts` 一致。
5. `unit_ts` 一致，或至少没有倒退到 manifest 之前未知状态。
6. `output_paths` 非空。

只要其中任一项不满足，就视为 manifest miss，直接走 full scan。

## Ingest 执行模型

### 路径 A：verified explicit fast path

触发条件：

1. 当前命令为 `--incremental`。
2. 命中 verified manifest，或拿到外部 provider 提供的可信 output paths。

执行顺序：

1. 读取 incremental state。
2. 打印诊断上下文。
3. 读取并校验 manifest。
4. 以 explicit 模式启动 Swift reader：
   - `useExplicitOutputUnits: true`
   - `addUnitOutFilePaths(output_paths)`
5. 导入完成后读取结果统计。
6. 执行 `>= baseline` 校验。
7. 校验通过：
   - 接受结果
   - 刷新 manifest 的 baseline 和元数据
8. 校验失败：
   - 标记 fast path failed
   - 立即进入 full scan fallback

### 路径 B：fallback full scan

触发条件：

1. 没有 manifest。
2. manifest mismatch。
3. manifest 为空或损坏。
4. explicit reader 执行失败。
5. 质量校验失败。

执行顺序：

1. 走当前稳定 full scan 路径。
2. 导入完成后读取结果统计。
3. 将该结果写成新的 verified manifest：
   - `output_paths`
   - `last_ingest_ts`
   - `unit_ts`
   - `quality_baseline`

## 关键前提

### 1. Orchard 需要有可信 output path 来源

如果没有外部 provider，Orchard 仍然可以靠“上一次 full scan 产出的 verified manifest”在第二次及以后启用 explicit fast path。

这意味着：

- 第一次 ingest 可以慢。
- 第二次及以后才是主要收益区间。

### 2. Swift reader 必须支持双模式

reader 层应该只暴露两种模式：

1. `fullScan`
2. `explicit(outputPaths: [String])`

`explicit` 模式之外，不允许半显式、半推导式行为混入主流程。

### 3. explicit DB 与 full-scan DB 需要隔离

为避免旧实验或错误 explicit 导致持久化 DB 污染：

1. explicit 模式使用独立 DB namespace。
2. full-scan 模式使用独立 DB namespace。
3. schema/version 变化必须体现在 DB key 中。

## 质量校验

### 第一版规则

explicit 完成后，取当前结果统计：

- `current.symbols`
- `current.relations`
- `current.occurrences`

与 baseline 做严格比较：

```text
if current.symbols < baseline.symbols: fallback
if current.relations < baseline.relations: fallback
if current.occurrences < baseline.occurrences: fallback
otherwise: accept
```

### 为什么不用比例阈值

本项目当前目标不是“绝大多数场景不退化”，而是“默认 incremental 不能接受任何已知质量下降”。

因此第一版使用硬门槛比比例阈值更符合预期，也更容易诊断。

### 后续可扩展项

若后续需要增强鲁棒性，可在不改变 `>= baseline` 主规则的前提下，再加入：

1. 关键文件 symbol count 哨兵。
2. 关键 ObjC/SDK/宏符号存在性哨兵。
3. per-target baseline。

这些不属于本轮必需项。

## 诊断输出

在已有 ingest 诊断基础上，建议补充以下字段：

- `state path`
- `last_ingest_ts`
- `selected index-store`
- `unit_ts`
- `manifest path`
- `manifest hit: yes/no`
- `manifest mismatch reason`
- `reader mode: explicit/full-scan`
- `quality baseline`
- `quality result`
- `fast path accepted: yes/no`
- `fallback triggered: yes/no`

日志目标不是详细审计，而是让用户能一眼判断“这次为什么没走 fast path”。

## 实现拆分

### Python 侧

1. manifest 数据结构与读写。
2. incremental 决策层：
   - `try_verified_explicit_fast_path`
   - `fallback_full_scan`
3. 质量校验逻辑。
4. 诊断输出与失败原因整理。

### Swift reader 侧

1. `fullScan` / `explicit` 双模式入口。
2. explicit 模式下启用：
   - `useExplicitOutputUnits`
   - `addUnitOutFilePaths(...)`
3. 输出结果统计供 Python 做 baseline 校验。
4. explicit/full-scan DB namespace 隔离。

## 测试策略

### P0 测试

1. 没有 manifest 时，`--incremental` 自动走 full scan。
2. manifest 命中时，reader 走 explicit 模式。
3. explicit 结果任意一项小于 baseline 时，自动 fallback。
4. fallback 后会写出新的 verified manifest。
5. explicit 与 full-scan DB 不共用同一路径。

### P1 测试

1. 使用真实 Zoom IndexStore 回归测试验证：
   - explicit 不得低于 baseline
   - 若低于则必须 fallback
2. state / manifest mismatch 时，日志明确指出 miss reason。

## 验收标准

1. `orchard ingest --incremental` 在有 verified manifest 的情况下，默认优先尝试 explicit fast path。
2. 任何 `symbols` / `relations` / `occurrences` 低于 baseline 的 explicit 结果都不会被接受。
3. fast path 失败后，系统自动回退 full scan，并能恢复到不降质结果。
4. 不存在任何本地 output-path 推导器参与主决策路径。
5. 日志足够解释本次 ingest 命中、miss、fallback 的原因。

## 开放问题

1. manifest 中的 `output_paths` 从哪里生成：
   - 若有外部 provider，则直接消费 provider 输出。
   - 若无 provider，则需要由 full scan 成功后的系统内记录产出。
2. `unit_ts` 的定义和稳定性边界是否足够覆盖所有真实 IndexStore 变化。
3. 是否需要在后续版本中引入关键文件/关键符号哨兵，作为 `>= baseline` 之外的第二道护栏。
