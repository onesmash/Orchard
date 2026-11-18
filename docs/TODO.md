# Orchard TODO — 图谱正确性与派生链路

> 本清单来自 2026-11-18 的四次代码研究（IndexStore 两层差异 / 图数据库必要性 / 四类增量实现 / 关系表示）。
> 每条都带**代码锚点**，锚点已按 `origin/main @7ad3e0a` 复核；未验证的假设单独标注，不混进事实。
>
> 状态：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞
> 优先级：**P0** 会产生错误答案或丢数据 · **P1** 答案精度/可信度 · **P2** 未知与能力缺口
> 规模：P0 4 条 · P1 10 条 · P2 5 条

## 概览

| ID | 主题 | 优先级 | 状态 | 锚点 |
| --- | --- | --- | --- | --- |
| T-01 | 方向语义已确认，但模块注释写反、无回归测试 | P1 | [ ] | `identity.py:149`、`b13ec2b` |
| T-02 | `Declares` 是死表，`memberOf` 端点类型不匹配 | P0 | [ ] | `schema.py:90`、`identity.py:140` |
| T-03 | `References` 无占位节点，静默丢边 | P0 | [ ] | `identity.py:414` |
| T-04 | `kind` 双轨：`swift.method` vs `method` | P0 | [ ] | `runner.py:37`、`symbolgraph.py:49` |
| T-05 | 派生阶段分裂在 runner 与 cli 两个入口 | P0 | [ ] | `runner.py:230`、`cli.py:588` |
| T-06 | 属性 read/write 角色不落图 | P1 | [ ] | `identity.py:414` |
| T-07 | `staticProperty`/`classProperty` 被压成 `instanceProperty` | P1 | [ ] | `runner.py:46` |
| T-08 | `_find_owner` 用「第一个 View」兜底，会造伪边 | P1 | [ ] | `swiftui.py:101` |
| T-09 | DFS 环计数按回边累加，且恒返回 `False` | P1 | [ ] | `architecture.py:131` |
| T-10 | 置信度硬编码（0.90/0.75/0.70/0.95），未校准 | P1 | [ ] | `identity.py:321`、`bridge.py:309` |
| T-11 | `find_references` 实际走 `Calls`，名不副实 | P1 | [ ] | `references.py:27` |
| T-12 | 子类型闭包计入 d1，风险被夸大 | P1 | [ ] | `impact.py:200`、`impact.py:266` |
| T-13 | freshness 风险策略与设计文档不一致 | P1 | [ ] | `impact.py:45`、`docs:1168` |
| T-14 | 术语一致性：`Implements`=override、`References` 无人查 | P1 | [ ] | `identity.py:144`、`references.py:27` |
| T-15 | 缺「按需多跳 vs 图查询」基准 | P2 | [ ] | 见下 |
| T-16 | IndexStoreDB record 缓存行为未知 | P2 | [ ] | 见下 |
| T-17 | 多 store / 多 target 合并代价未测 | P2 | [ ] | 见下 |
| T-18 | salsa 式按需路线未做 spike | P2 | [ ] | 见下 |
| T-19 | 运行期测试与 CI 缺失 | P2 | [ ] | 无 `.github/` |

---

## P0 — 正确性

### T-02 `Declares` 死表 + `memberOf` 端点不匹配

**现状**
- `schema.py:90` 定义 `Declares(FROM File TO Symbol)`。
- 全仓库没有任何写入 `Declares` 的代码；唯一引用是 `_REL_KIND_TO_TABLE["memberOf"] = "Declares"`，而 SymbolGraph 的 `memberOf` 两端都是 Symbol（成员 → 容器）。
- 查询端成员枚举用的是 `Contains`，不读 `Declares`。

**影响**：要么 `MERGE (a:Symbol)-[:Declares]->(b:Symbol)` 因端点类型不符报错/被丢弃，要么留下语义错误的边；两种情况都无法提供「文件声明了哪些符号」的能力。

**动作**：二选一并在 schema 注释里写明——(a) `memberOf` 改映射到 `Contains`，`Declares` 标记 deprecated；(b) 补上真正的 File→Symbol 写入（从 `Symbol.file_path` 生成），`memberOf` 单独处理。

**验收**：新增 `tests/test_graph/test_schema_contract.py`，断言每张 rel 表要么有写入者、要么显式标 deprecated；一次含 `memberOf` 的 symbolgraph ingest 不报错且边数符合预期。

### T-03 `References` 静默丢边

**现状**：`upsert_references` 用 `MATCH (a:Symbol {id}), (b:Symbol {id}) MERGE`，要求两端已存在，且**不建占位节点**；`Calls` 与结构边都有 `_ensure_symbol_ids_exist` 兜底。

**影响**：引用 SDK 符号或未索引符号时边直接消失，无日志；`architecture_derivation` 用 `References` 算跨模块依赖，会少算依赖。

**动作**：复用占位节点机制（与 `Calls` 对齐），或改为 CSV `COPY` 批量写；统计并打印 `dropped`。

**验收**：新增测试覆盖「目标符号不存在」场景，断言边仍写入；ingest 日志出现 `references_dropped` 计数。

### T-04 `kind` 双轨

**现状**
- IndexStore 路径把 `instancemethod` → `method`、`instanceproperty` → `instanceProperty`（`_map_indexstore_kind`）。
- SymbolGraph 路径原样保留 `swift.method` / `swift.property`，中间没有归一。
- 合并时 SymbolGraph 记录优先，`kind` 不会被 IndexStore 覆盖。

**影响**：`methods_of`（`WHERE child.kind='method'`）、SwiftUI 派生（`kind IN ['instanceProperty','instanceMethod']`）、`owner_of`（owner kind 白名单）在 SymbolGraph 覆盖的符号上返回空——**静默的空结果比报错更贵**。

**动作**：新增 `normalize_symbol_kind(source, raw_kind)`，在 identity 归一阶段统一（去 `swift.`/`clang.` 前缀并映射）；或按来源分列存 `kind_raw` + `kind`。

**验收**：新增测试：只喂 SymbolGraph 的符号，`methods_of` 能返回成员；`uv run pytest tests/test_query -q` 全绿。

### T-05 派生阶段分裂在两个入口

**现状**：`pipeline.run_ingest_pipeline` 只跑 bridge / architecture / swiftui；community / notification / processes 只在 `cli.cmd_ingest` 追加。

**影响**：任何走 pipeline 的调用方（测试夹具、MCP 预热、未来 server 启动路径）都拿到半成品图谱，且没有告警；不同入口产出的图不一致，会让「同一工程两次分析结果不同」。

**动作**：抽出单一编排函数（如 `run_derivations(conn, ctx, changed_files=None)`），runner 与 cli 都调用；补齐派生清单的单一事实源。

**验收**：新增 `tests/test_pipeline/test_derivation_parity.py`，断言两种入口产出的边类型集合一致；usage guide 同步说明。

---

## P1 — 语义精度

### T-01 方向语义已被数据确认，但注释写反、且无回归测试

**现状**
- `b13ec2b`（2026-06-25）用原始 CLI 数据确认了方向：`childOf(from=method, to=extension)` 说明「关联符号作用于 occurrence」，因此 `Contains(extension → method)` = `Contains(to → from)`；提交说明称 `baseOf`/`overrideOf`/`containedBy`/`extendedBy` 按同一模式处理（后续 `4e69433`、`bc8732a` 又迭代过）。
- 但 `_INDEXSTORE_REL_TO_TABLE` 上方的注释仍写 `baseOf(from_usr=Derived, to_usr=Base) means Derived inherits from Base → Inherits(Derived → Base)`，与实现（写入 `(t_id, s_id)`）及该提交结论相反。
- `tests/` 中没有 `upsert_indexstore_rels` 的方向断言，只在 acceptance 测试里被 monkeypatch 掉。

**影响**：后来者按注释「修正」代码会把整棵继承/包含关系翻回去；`baseOf` 属于「同模式类推」，没有独立验证过。

**动作**
1. 把注释改成与实现一致的表述，并引用 `b13ec2b` 的验证依据。
2. 为四类结构边补 golden test（断言落库方向）。
3. 若可能，用 `c-index-test core -print-record` 或阅读器 JSONL 再独立验证一次 `baseOf`。

**验收**：注释与实现一致；新增 `tests/test_normalize/test_structural_edge_direction.py` 覆盖 `baseOf`/`childOf`/`overrideOf`/`extendedBy`；`uv run pytest tests/test_normalize -q` 全绿。

### T-06 属性 read/write 不落图
`RelationRecord.occurrence_role` 已存在，但只有 `Calls` 用它决定 reason；`References` 边只存 `source`。→ 图上无法回答「谁读了/写了这个属性」。
**动作**：`References` 增加 `role ∈ {read,write,reference}`，或新增 `Reads`/`Writes` 边；查询端支持过滤。
**验收**：单测断言同一属性可分别查出读者与写者。

### T-07 静态属性被压平
`_map_indexstore_kind` 把 `instanceProperty` / `staticProperty` / `classProperty` 统一成 `instanceProperty`。
**动作**：保留三态，或加 `is_static` 属性。
**验收**：三个 kind 的单测断言不再合并。

### T-08 `_find_owner` 兜底造假边
前缀匹配失败时返回「第一个 View」。
**动作**：改用 `Symbol.container_usr`；无法确定时不产边，或降 confidence 并标 `derived_from=fallback`。
**验收**：反例测试——body 与任何 View 无前缀关系时不产 `ViewTree` 边。

### T-09 DFS 环计数语义
`cycles_found` 对每条回边 +1（一个环可能多次计数），且 `dfs` 恒返回 `False`（返回值未被使用）。
**动作**：改为三色标记 + 每个 SCC/环只计一次，或直接输出 SCC 列表；清理无效返回值。
**验收**：三节点单环 → 1；两个互不相连的环 → 2；新增 `tests/test_derive/test_architecture_cycles.py`。

### T-10 置信度硬编码
结构边固定 `0.90`；`BridgesTo` 0.95/0.85/0.82/0.70；`ViewTree` 0.75；通知 0.70。无校准依据。
**动作**：集中到 `orchard/graph/confidence.py`；用 20 个人工标注样本报告每档 precision；在响应里区分「常量先验」与「实测校准」。
**验收**：`grep -R "0\.\(7\|8\|9\|95\)" src/orchard/derive` 不再出现散落字面量；校准报告入库。

### T-11 `find_references` 名不副实
实现返回 `Calls` 的 callers/callees，从不读 `References` 表。
**动作**：默认 `relation_types=["Calls","References"]`，或改名并在文档写清语义。
**验收**：测试断言两类边都能被返回；工具描述同步更新。

### T-12 子类型闭包计入 d1
`d1_subtypes` 直接塞进 `depths["d1"]`，`risk` 用 `len(d1)` 判档。
**影响**：只加子类不加调用方时，风险会被判高。
**动作**：子类型单独分组（或标 `counts_as_dependent=false`），风险只统计调用/引用直连。
**验收**：新增 `tests/test_handlers/test_impact_risk.py`：仅子类型场景 risk 保持 low。

### T-13 风险策略与文档不一致
实现：`freshness != fresh` 直接 `critical`（`impact.py:45`）；设计文档：至少上调一档（`docs/apple-semantic-grapgh.md:1168`）。
**动作**：统一语义（建议按文档「上调一档」，`critical` 仅用于高扇出 + 跨 target 场景）。
**验收**：三组案例测试（fresh/low、stale/medium、cross-target/stale）各断言档位。

### T-14 术语一致性
`Implements` 装 override；`References` 表无人查；`Declares` 未启用。
**动作**：在 `docs/apple-semantic-grapgh.md` 的 Edge 模型章节加「表名 ↔ 语义」对照表并标注历史包袱；对外文档避免用「Implements」指协议实现。
**验收**：文档章节存在且与 schema 一致。

---

## P2 — 未知与能力缺口

### T-15 缺「按需多跳 vs 图查询」基准
- **问题**：图方案的成本收益没有实测数字。
- **动作**：取 20 个热点 USR，跑 depth=3 的 callers 闭包，记录调用次数与墙钟；与一条 Cypher 对比。
- **验收**：基准脚本 + 报告。

### T-16 IndexStoreDB record 缓存行为未知
- **问题**：若 record 有内部缓存，「逐节点查询很贵」的推断会被削弱。
- **动作**：读 `SymbolDataProvider`/`StoreSymbolRecord`；或对同一 USR 重复查询 1000 次计时。
- **验收**：给出「命中/未命中」结论并回填 T-15 报告。

### T-17 多 store / 多 target 合并代价未测
- **问题**：`canonical` 只在单库内生效；多 target 合并后的符号冲突与边冗余未量化。
- **动作**：构造双 target 工程，测 ingest 时间、重复 USR 数、边冗余率。
- **验收**：数据表入档。

### T-18 salsa 式按需路线未 spike
- **问题**：rust-analyzer 证明「不物化图」也能做语义查询；本仓库的可行性未知。
- **动作**：最小 spike：IndexStoreDB + 内存缓存 + 惰性闭包，比较与图方案的延迟/复杂度。
- **验收**：spike 结论文档；决定是否保留图方案（预期保留，但需数据支撑）。

### T-19 运行期测试与 CI 缺失
- **问题**：仓库无 `.github/`，无 CI；测试需要 `ladybug` 等重依赖，`llama-cpp-python` 进一步抬高本地门槛。
- **动作**：加 CI `test` job；把 embedder 依赖做成可选（extras 或惰性导入），让核心测试可在无 llama-cpp 环境跑。
- **验收**：CI 绿；本地按下方命令可在 1 分钟内跑完图/派生测试。

```bash
# 完整环境（llama-cpp-python 可能编译较久）
uv sync --group dev && uv run pytest -q

# 轻量环境：只装图谱与测试依赖
uv pip install igraph ladybug leidenalg pytest pytest-asyncio
PYTHONPATH=src python -m pytest tests/test_normalize tests/test_graph tests/test_query tests/test_derive -q
```

---

## 明确不做

1. **不为上述问题引入重型图库**（Neo4j 类）。当前瓶颈是正确性与派生完整度，不是遍历性能；只有当 T-15 的基准显示深层遍历成为瓶颈时再评估。
2. **不把 IndexStoreDB 的 occurrence 全量复制进图**。新增一份事实副本 = 新增一条漂移路径；图只存索引、派生边与元数据。
3. **不基于字符串启发式继续加派生边种类**，直到 T-10 的置信度校准机制建立。宁可少一条边，不要多一条假边。

## 复核方式

```bash
# 方向/端点类问题
grep -rn "Declares" src tests
grep -n "_INDEXSTORE_REL_TO_TABLE" -A 10 src/orchard/normalize/identity.py

# kind 双轨
grep -rn "kind = 'method'\|kind IN \['instanceProperty'" src/orchard

# 派生入口
grep -rn "run_community_detection\|persist_notification_graph\|detect_processes" src/orchard

# 置信度硬编码
grep -rn "0\.90\|0\.75\|0\.70\|0\.95" src/orchard
```

## 关联资料

- 设计文档：[`apple-semantic-grapgh.md`](apple-semantic-grapgh.md)（Edge 模型、impact 遍历规则、风险分级）
- 使用文档：[`orchard-usage-guide.md`](orchard-usage-guide.md)
- 方向语义的原始验证提交：`b13ec2b`（`childOf(from=method, to=extension)` → `Contains(to → from)`）
