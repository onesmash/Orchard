# Orchard Optimization Batch 3

> 状态：已确认方向。用户已选择“激进批次”，本轮以 `/tmp/orchard-optimization-handoff.md` 为输入，优先修复 P0/P1 中当前仓库可直接实现与验证的问题。

## 目标

本轮优化 Orchard 的三类核心问题：

1. 数据正确性：避免 SymbolGraph 覆盖 IndexStore 的正确信息，减少查询结果误导。
2. ingest / CLI 可用性：让自动发现、路径推断、source-root 过滤和 DB 定位行为更可解释。
3. 查询呈现与 freshness：让 CLI 输出足够自描述，避免默认全部显示 `stale`。

## 本轮范围

### In Scope

1. `swift_symbolgraph` 与 `IndexStore` 的字段级合并
   - 解决 handoff #1
   - 同一 USR 同时存在两路来源时，不再“二选一”
   - `file_path` 优先使用 IndexStore 的定义位置
   - `name` 优先使用 IndexStore 的原生名称，避免 ObjC 被 Swift 桥接名覆盖
   - 为后续 bridge / dual-name 展示保留扩展位

2. `find_callers` 返回补全的位置信息
   - 解决 handoff #4
   - 输出补充 `file_path`
   - 尽量补充定义位置的 `line` / `col`
   - 保持现有响应结构兼容，新增字段走增量扩展

3. `--source-root` 的健壮性与空结果诊断
   - 解决 handoff #6
   - 传入 IndexStore 前先标准化为绝对路径
   - 若过滤后结果为 0，输出明确 warning，指出可能是路径前缀不匹配

4. ingest 的 DB 路径推断与查询时 DB 发现提示
   - 解决 handoff #7
   - `orchard ingest` 默认 DB 路径应锚定到实际 Xcode 项目目录，而不是调用时 cwd
   - 查询命令在使用父目录发现到的 `.orchard/graph.db` 时要打印提示
   - `stats` 至少打印当前连接的 DB 路径

5. `stats` 输出补全构建元信息
   - 解决 handoff #9
   - 增加 DB 路径、最新 BuildSnapshot、IndexStore 路径、创建时间、commit、freshness 状态

6. CLI 查询默认 freshness 修复
   - 解决 handoff #10
   - CLI 查询在未显式给 build_id 时，默认绑定“当前 target 最新 BuildSnapshot”
   - 避免空 build_id 直接导致统一 `stale`

7. ObjC / Swift bridge 的第一阶段统一展示
   - 覆盖 handoff #8 的一部分前置条件
   - 本轮先让符号主名称优先采用 IndexStore 原生名
   - 若 schema / 查询成本可控，可补充 `swift_display_name` 一类字段或等价展示来源
   - 不要求本轮完整重做 USR-based `BridgesTo` 恢复算法

### Phase 2 / Stretch Goals

以下项目不是放弃，而是按实现风险和验证成本拆成第二阶段连续推进项；本轮不提前 descoped，若主线项完成并且验证顺利，则继续落代码：

1. 过期 USR 清理完整机制
   - 对应 handoff #2
   - 目标仍然是实现 ingest 后的旧 USR 清理或等价的 snapshot-based 失效机制
   - 之所以放到第二阶段，是因为它会牵涉删除策略、BuildSnapshot 生命周期和历史数据兼容

2. `find_callers` 中“源码直接调用 vs 编译内联调用”的语义重建
   - 对应 handoff #3
   - 目标仍然是区分源码级直接调用与编译期 inline 导致的直接 Calls 边
   - 若当前 IndexStore 证据不足，则本轮至少要把问题边界、可检测信号和后续实现入口补齐

3. 完整的 USR-based bridge recovery 重写
   - 对应 handoff #8 的第二阶段
   - 本轮主线先通过字段级合并修复名称展示和路径错误
   - 在此基础上继续评估是否将 `derive/bridge.py` 从 name match 推进到 USR-based 恢复

## 执行策略

### Phase 1 主线必做

1. `#1` SymbolGraph / IndexStore 字段级合并
2. `#4` `find_callers` 位置字段补全
3. `#6` `--source-root` 标准化与 0 结果诊断
4. `#7` DB 路径推断与查询 DB 发现提示
5. `#9` `stats` 构建元信息补全
6. `#10` CLI 默认 freshness 修复
7. `#8` 第一阶段桥接名称统一展示

### Phase 2 连续推进

1. `#2` 过期 USR 清理
2. `#3` inline 调用语义澄清 / 标注
3. `#8` 第二阶段 USR-based bridge recovery

## 设计决策

### 1. ingest 改成“双来源合并”，不是“单来源 fallback”

现状问题：
- `runner.py` 仅在没有 SymbolGraph 时才退回 IndexStore symbol descriptors
- `identity.py` 的 `upsert_symbols()` 只插入新符号，不更新已有字段
- 一旦 SymbolGraph 先写入，同 USR 的 IndexStore 正确信息就无法覆盖

决策：
- 在 ingest 阶段先按 `USR` 聚合同 target 的符号
- 构造统一的合并结果再进入 `upsert_symbols()`
- 字段优先级：
  - `file_path`: IndexStore > SymbolGraph
  - `name`: IndexStore > SymbolGraph
  - `kind`: SymbolGraph > IndexStore 映射结果
  - `module`: 优先非空
  - `language`: 优先非空
- 如某字段两边都为空，保持空值，不做猜测性推断

理由：
- 这是修复路径错误、桥接名错位的最小闭环
- 比“先插再 UPDATE”更容易测试，也更容易保持幂等

### 2. CLI 行为优先“可解释”，其次才是“静默聪明”

现状问题：
- ingest 默认 DB 路径取 cwd，可能与实际 Xcode 项目根错位
- 查询 walk-up 命中父目录 DB 时完全静默
- `source-root` 过滤为 0 时无诊断

决策：
- 默认路径、自动发现和统计命令全部显式打印关键上下文
- 遇到“看起来成功但结果可疑”的情况，优先给 warning

理由：
- 这类问题的根本痛点不是功能缺失，而是“用户不知道 Orchard 正在连什么、过滤了什么”

### 3. freshness 默认绑定“最新可用 build”

现状问题：
- handler 支持 `build_id`
- 但 CLI 请求对象未传，最终 `freshness_for("", {})` 恒为 `stale`

决策：
- 加一个查询侧 helper，从当前 target 解析最新 BuildSnapshot
- CLI 未传 `build_id` 时自动填充
- 若解析不到 build，再保留 `stale`

理由：
- 这是最符合用户直觉的默认行为
- 不改变 handler 的协议，只修复 CLI 装配层

## 计划落点

### 预计改动文件

- `src/orchard/pipeline/runner.py`
- `src/orchard/normalize/identity.py`
- `src/orchard/cli.py`
- `src/orchard/query/lookup.py`
- `src/orchard/handlers/callers.py`
- `src/orchard/build/xcode_settings.py`
- `src/orchard/validation/freshness.py`
- 相关测试文件

### 测试重点

1. 同 USR 的 SymbolGraph / IndexStore 合并后，IndexStore `file_path` 与 `name` 胜出
2. `find_callers` 响应包含新增位置字段，旧字段不回退
3. `--source-root .` 这类相对路径会被标准化，0 结果时有 warning
4. ingest 默认 DB 路径锚定在真实 Xcode 项目目录
5. 非 cwd 命中的 DB 会有提示
6. `stats` 会输出 BuildSnapshot 元信息
7. CLI 未显式传 `build_id` 时，freshness 不再无条件为 `stale`

## 验收标准

1. 手头测试和单元测试能够稳定证明：
   - 路径错误不再被 SymbolGraph 锁死
   - freshness 默认值恢复可用
   - CLI / stats 输出具备定位问题所需的最小上下文
2. Phase 1 主线项必须形成闭环，并在时间和验证预算允许时继续推进 Phase 2。
3. 即使 Phase 2 某些深层项未完全收口，最终说明也必须明确做到哪一层、剩余风险在哪里，避免误报“已全部修复”。

## 开放问题

1. `swift_display_name` 是否本轮入库，还是先仅通过现有 bridge / 查询层暴露，取决于 schema 改动成本。
2. `find_callers` 的 `line` / `col` 若无法稳定从当前图模型精确回填，可先返回 `file_path` 并对行列补充保守降级。
3. 若 `BuildSnapshot` 与 `target` 的关联在现有数据里不完整，freshness 默认 build 的解析逻辑需要以“最新 snapshot”作为兼容退路。
