# Design Perspective Review -- Orchard GitNexus Pattern Optimizations

**Date**: 2026-06-25
**Reviewer**: Architecture design agent
**Spec**: `2026-06-25-orchard-gitnexus-optimizations.md`
**References**: `GitNexus/ARCHITECTURE.md`, `GitNexus/gitnexus/src/core/graph/graph.ts`, `GitNexus/gitnexus/src/core/graph/types.ts`

## 总分：3.2/5

## 各维度评分

| 维度 | 分数 | 关键发现 |
|---|---|---|
| Pattern Faithfulness | 2.5/5 | 6/8 模式翻译基本正确，#7 增量索引并非来自 GitNexus（GitNexus 做全量重建），#5 Process/Community 依赖关系颠倒 |
| Naming Consistency | 2.5/5 | 3 个文件用 hyphen 而非 snake_case；`ingestion/` 与现有 `ingest/` 冲突；`knowledge_graph.py` 与已有 `graph/` 包命名不一致 |
| Priority Ordering | 3.0/5 | P0/P1/P2 分层合理，但 #5 Process 依赖 #8 Communities 存在实现顺序颠倒；#6 Contract Extractor 风险高却在 P1 |
| Python/Ladybug Adaptation | 3.5/5 | TypeScript→Python 翻译方向正确，但 KnowledgeGraph 公开 API 未详述；"COPY FROM" 需确认 LadybugDB 是否支持 |
| Completeness | 3.0/5 | 遗漏 4 个 GitNexus 关键模式：single CodeRelation table、SemanticModel write/read contract、MRO 阶段、per-type 迭代器设计 |

## 详细分析

### Q1：Pattern 翻译是否忠实于 GitNexus 原始设计？

#### Pattern #1: In-Memory KnowledgeGraph + Dual Indexing -- 忠实度：中

**源实现**（`graph.ts:11-182` + `types.ts:12-38`）：

GitNexus 的 `createKnowledgeGraph()` 返回一个闭包对象，包含完整的公开 API：
- 数据结构：`nodeMap: Map<id, GraphNode>`, `relationshipMap: Map<id, GraphRelationship>`, `relationshipsByType: Map<RelationshipType, Map<id, Rel>>`, `edgeIdsByNode: Map<nodeId, Set<relId>>`, `nodeIdsByFile: Map<filePath, Set<nodeId>>`
- 查询 API：`getNode(id)`, `nodeCount` (getter, O(1)), `relationshipCount` (getter, O(1)), `iterNodes()`, `iterRelationships()`, `iterRelationshipsByType(type)` (hot-path 优化), `forEachNode(fn)`, `forEachRelationship(fn)`
- 变更 API：`addNode`, `addRelationship`, `removeNode( O(edges-touching-node))`, `removeNodesByFile(O(file-nodes))`, `removeRelationship`

**Spec 翻译**（spec line 8）：
```python
KnowledgeGraph class: node_map: dict, rel_map: dict, rels_by_type: dict,
edge_ids_by_node: dict, node_ids_by_file: dict
```

**问题**：
1. **只描述内部数据结构，未定义公开 API**。GitNexus 的 KnowledgeGraph 是一个精心设计的接口（`types.ts` 有完整的 `KnowledgeGraph` interface），spec 只列出内部 dict 字段，缺少所有 mutation/query 方法签名。`removeNode` 和 `removeNodesByFile` 对增量索引至关重要，不应遗漏。

2. **`rel_map` 命名不精确**。GitNexus 用 `relationshipMap`（全称），orchard 现有代码倾向全称命名（`SymbolLineRecord`、`BuildContext`）。建议用 `relationship_map`。

3. **`rels_by_type` 的桶类型未指定**。GitNexus 用 `Map<RelationshipType, Map<id, Relationship>>` —— 内层是 Map（O(1) 删除），而非 Set/List。这很关键：如果用 `dict[str, set[str]]` 存 rel id 集合，删除边时无法 O(1) 获取关系本身。

4. **写入 LadybugDB 的方式**。Spec 说 "flush to LadybugDB via COPY FROM"，但 GitNexus 使用 `loadGraphToLbug()` 的 **CSV streaming** 方式（ARCHITECTURE.md line 368）。需确认 LadybugDB Python SDK 是否支持 COPY FROM 或需要改用批量 INSERT/CREATE。

5. **缺失 `iterRelationshipsByType` 设计说明**。这是 GitNexus MRO/heritage 热路径的关键优化 —— 避免扫描所有关系再按 type 过滤。spec 的 `rels_by_type: dict` 暗示有这个索引，但未说明如何暴露遍历接口。

6. **缺失 O(1) nodeCount/relationshipCount**。GitNexus 用 getter 属性避免 `Array.from()` 创建数组的开销。Python 应提供 `__len__` 或 `node_count` 属性。

#### Pattern #2: Per-Edge confidence + reason -- 忠实度：中高

**源实现**：GitNexus 的 `CodeRelation` 表原生携带 `confidence DOUBLE` + `reason STRING` 列。confidence 有精细化 tier：import resolution (0.95/0.9/0.5)，variadic matching (0.7)，exact type match (1.0)。reason 编码远超 provenance —— 包含 CFG edge kind、reaching-def 变量名、CDG branch sense。

**Spec 翻译**：在 orchard 所有 rel 表上加 `confidence DOUBLE` + `reason STRING`。

**问题**：
1. **部分表已有这些列**。orchard 的 `Calls` 表已有 `confidence DOUBLE` + `provenance STRING`（schema.py:92-95）；`BridgesTo` 已有 `confidence DOUBLE` + `provenance STRING`；`ViewTree` 和 `NavigationFlow` 已有 `confidence DOUBLE` + `derived_from STRING`。spec 应区分"已有需修改"和"全新添加"，避免重复创建列。

2. **`reason` vs 已有 `source`/`provenance`/`derived_from` 列**。orchard 现有模式用不同列名区分来源语义：`Calls.source`、`Calls.provenance`、`BridgesTo.provenance`、`ViewTree.derived_from`。统一加 `reason` 后，旧的 `source`/`provenance` 列如何处理？是共存还是迁移？spec 未说明迁移策略。

3. **confidence 值映射**。Spec 给出 indexstore=0.90, bridge=0.70/0.85, swiftui=0.80。这些值与 GitNexus 的 tier 没有直接对应关系（GitNexus 的 0.95/0.9/0.5 来自 import resolution 而非数据源）。差异本身合理（不同数据源有不同置信度），但需要文档说明选择依据。

#### Pattern #3: Pipeline DAG + Kahn Topological Sort -- 忠实度：中

**源实现**（`runner.ts` + `ARCHITECTURE.md:111-130`）：
- 静态阶段图，编译时类型安全
- Kahn 算法验证：拒绝重复名/缺失依赖/环路（DFS 追踪具体环路路径 + 被阻塞的依赖数）
- 顺序执行，每阶段接收 `ctx: PipelineContext` + `deps: ReadonlyMap<string, PhaseResult>`（**仅声明依赖**，runner 过滤防止隐藏耦合）
- 错误处理：包装阶段错误（附阶段名），发出终端 `error` 进度事件
- 每阶段 `durationMs` 计时

**Spec 翻译**：`PipelinePhase` protocol + Kahn runner + `enabledWhen` predicate。

**问题**：
1. **orchard 已有 pipeline runner**。orchard 的 `pipeline/runner.py` 是手写顺序执行 + `asyncio.gather` 并发（line 94-96），不是 DAG。spec 说 "Add" 但实际上是**完全替换**现有 runner。需要说明迁移路径。

2. **缺失 GitNexus 的安全措施**：
   - GitNexus runner 过滤 deps 为仅声明依赖 —— 防止阶段间隐藏耦合。spec 未提及此约束。
   - GitNexus 对环路给出精确追踪（`A -> B -> C -> A` + N 个被阻塞阶段）。spec 只说 "cycle detection"。
   - GitNexus 包装阶段错误的阶段名便于调试。spec 未提及。

3. **`enabledWhen` predicate**。这是 spec 的原创扩展（GitNexus 用 `PipelineOptions.skipGraphPhases` boolean flag）。这是一个合理的 Pythonic 设计，但应该标注为 "extension beyond GitNexus"。

4. **并发执行**。GitNexus runner 是**纯顺序**执行（sequential in topological order）。但 orchard 现有 `pipeline/runner.py` 用 `asyncio.gather` 并发执行独立 I/O 阶段（indexstore + symbolgraph）。新的 DAG runner 是否保留并发能力？Kahn 算法天然支持并发执行无依赖的阶段 —— spec 应说明选择顺序还是并发执行的策略。

5. **`PipelinePhase` 用 Protocol 而非 ABC**。spec 选 Protocol（structural subtyping），合理 —— 与 dataclass 兼容。但 `name` 和 `deps` 是 class 属性还是 instance 属性？GitNexus 用 object literal `{name, deps, execute}`，Python 可考虑 `@dataclass` + Protocol 组合。

#### Pattern #4: RRF Hybrid Search -- 忠实度：高

**源实现**：BM25 + embedding vector，Reciprocal Rank Fusion K=60。

**Spec 翻译**：BM25 via LadybugDB FTS + embedding vector，RRF K=60。

**问题**：
1. **orchard 已有 hybrid search**。`handlers/semantic_search.py` 实现了 cosine 相似度 + substring FTS 的简单合并（hardcoded 0.5 fallback score），不是真正的 BM25/RRF。spec 说 "Integrate into `semantic_search` handler"，应说明迁移路径：是 refactor 现有代码还是并行新增。

2. **FTS 索引目标未明确**。GitNexus FTS 建在 LadybugDB 的 File/Function/Class/Method 等**节点表**上（ARCHITECTURE.md line 369），而非 embedding 表。spec 说 "LadybugDB FTS extension" 但未说明在哪些列上建 FTS 索引。

3. **BM25 在 LadybugDB 的支持**。需确认 LadybugDB 的 FTS 扩展是否原生支持 BM25 评分，还是需要应用层实现。这与 GitNexus 的 LadybugDB FTS 是同一基础设施，应可直接复用。

4. **嵌入维度差异**。GitNexus 用 Snowflake arctic-embed-xs (384D)，orchard 用 qwen3-embedding (1024D)。搜索质量差异不是设计 review 的问题，但 RRF K=60 对这个维度差异的敏感性应简要提及。

#### Pattern #5: Process Detection -- 忠实度：中低

**源实现**（`processes.ts`, ARCHITECTURE.md line 107）：
- 依赖 `communities`, `routes`, `tools`, `pruneLocalSymbols`, `structure`
- 找到入口点（无内部调用者的函数）
- BFS 前向遍历 Calls
- 分组相似路径，去重
- 启发式标签从函数名生成
- 写入 `Process` 节点 + `STEP_IN_PROCESS` 边 + `ENTRY_POINT_OF` 边

**Spec 翻译**：同上逻辑，但不提对 communities 的依赖。

**问题**：
1. **依赖顺序颠倒**。GitNexus 的 processes 阶段**排在 communities 之后**（参见 DAG: `communities → processes`）。spec 的 implementation order 把 #5 process 放在 phase 5，把 #8 communities 放在 phase 7 —— 完全反了。如果 process 需要 community 信息来生成有意义的标签或分组，这个顺序错误会导致返工。

2. **缺失 `ENTRY_POINT_OF` 边**。GitNexus 同时生成 `STEP_IN_PROCESS` 和 `ENTRY_POINT_OF`。spec 只提 `STEP_IN_PROCESS`。

3. **iOS 特有的入口点检测**。GitNexus 的入口点检测（无内部调用者）适用于通用代码库。iOS 项目有特有的入口点模式：`@main` / `UIApplicationDelegate` / `SwiftUI App` / `UIViewController` 生命周期方法。spec 未讨论这些 iOS 特有的启发式入口点检测。

#### Pattern #6: Contract Extractor -- 忠实度：低

**源实现**：GitNexus 从 HTTP route handlers 提取 API contracts，生成 `Contract` 条目，跨 repo 匹配通过 `groups/` subsystem。

**Spec 翻译**：从 IndexStore symbols（public methods、protocols）提取 contracts，跨 target 匹配。

**问题**：
1. **这是根本性的重新解释，而非翻译**。GitNexus contracts 是 **HTTP API 契约**（路由、请求/响应 shape），spec 把它变成了 **代码级 API 契约**（public 方法签名）。这两种 contract 的语义完全不同。spec 应标注为 "启发自 GitNexus 的契约概念，但语义域完全不同"。

2. **缺少设计细节**。GitNexus 的 Contract 系统包含 `ContractRegistry`、normalized contract ID、cross-linking 等基础设施（`group/contracts.json`）。spec 只说 "Extract API contracts from IndexStore symbols" 但未定义 contract 的 schema、normalization 规则、或跨 target 匹配的策略。

3. **可行性存疑**。IndexStore 的符号信息（USR、name、kind）可以形成"公共接口列表"，但真正的 API 兼容性检查需要类型签名、参数顺序、返回值类型等。仅仅有 USR 和 name 不足以做有意义的契约匹配。spec 的风险评级是 "High"，这与设计复杂度匹配，但 P1 优先级值得商榷。

#### Pattern #7: Incremental Indexing -- 忠实度：N/A（非 GitNexus 模式）

**源实现**：GitNexus **不做增量索引**。`runFullAnalysis`（ARCHITECTURE.md line 360）是全量 pipeline 重建。如果 `lastCommit == HEAD`且非 `--force`，直接 exit。GitNexus 有 `detect_changes` MCP tool 用于映射 diff 到受影响的符号和流程，但这**不是增量索引** —— 它不改图，只是分析影响。

**Spec**：`shadow_candidates()` + `subgraph_extract()` + 仅重建变化部分。

**问题**：
1. **这是原创模式，不应标注为"borrowed from GitNexus"**。spec 开篇声称 "Borrows 8 patterns from GitNexus"，但此模式在 GitNexus 中不存在。GitNexus 有 `removeNodesByFile` / `removeNode` 方法（用于图编辑），但没有增量重建策略。应重新归类为 "inspired by orchard's own needs" 或引用其他系统的增量索引模式。

2. **设计细节严重不足**。增量索引是复杂问题：改变了文件 A，需要重索引 A 的符号；但如果 A 的符号被 B 引用，B 的引用边是否仍然有效？如果 A 删除了函数 f，而 B 调用 f，B 的调用边需不需要更新？spec 仅提了 `shadow_candidates` 和 `subgraph_extract` 两个函数名，没有设计这些核心语义。

3. **与 GitNexus 的 `removeNodesByFile` 关系**。Spec 的 KG #1 应包含 `removeNodesByFile` 方法（GitNexus 的核心 mutation API），这正是增量索引的基础设施。但 spec #1 没有列出它。

#### Pattern #8: Leiden Community Detection -- 忠实度：高

**源实现**（`communities.ts`）：Leiden 算法聚类 → `Community` 节点 + `MEMBER_OF` 边。依赖 `mro`, `pruneLocalSymbols`, `structure`。

**Spec 翻译**：基本正确。

**问题**：
1. **缺失 MRO 依赖**。GitNexus 的 communities 阶段依赖 `mro`（MRO 必须先完成以确保 CALLS 边完整）。orchard 没有显式的 MRO 阶段 —— 是否需要？在 Swift/ObjC 中，方法重写是常见的（protocol 遵循、继承链），缺失 MRO 可能导致社区检测的调用图不完整。

2. **实现库差异**。GitNexus 在 TypeScript/Node.js 中运行 Leiden 算法；Python 有 `leidenalg`（通过 `python-igraph`）。API 会不同但功能等效。实现时应文档化算法参数选择（resolution parameter, number of iterations 等）。

---

### Q2：命名是否符合 orchard 惯例？

| Spec 文件 | orchard 惯例 | 匹配？| 建议 |
|---|---|---|---|
| `knowledge_graph.py` | `graph/db.py`, `graph/schema.py` | 是 | graph 包下合理 |
| `schema.py` (修改) | `graph/schema.py` | 是 | 已存在，修改即可 |
| `phase.py` | `pipeline/runner.py` | 部分 | orchard 现有 pipeline 无独立 phase 定义文件。`phase.py` 作为新文件合理，但应放 `pipeline/` 下 |
| `runner.py` | `pipeline/runner.py` 已存在 | **冲突** | 同名文件将覆盖现有 runner。需要不同文件名（如 `dag_runner.py`）或替换现有 |
| `registry.py` | `pipeline/` 下无此文件 | 新文件 | 命名合理 |
| `hybrid_search.py` | `search/chunker.py`, `search/embedder.py` | 是 | search 包下合理 |
| `process-processor.py` | **蛇形命名** (`indexstore.py`, `symbolgraph.py`, `navigation_flow.py`) | **否** | 应改为 `process_processor.py` |
| `contract-extractor.py` | **蛇形命名** | **否** | 应改为 `contract_extractor.py` |
| `shadow.py` | 单词语义 (`bridge.py`, `embedder.py`) | 是 | 命名合理 |
| `subgraph.py` | 同上 | 是 | 命名合理 |
| `community-processor.py` | **蛇形命名** | **否** | 应改为 `community_processor.py` |

**严重问题**：

1. **`src/orchard/ingestion/` vs 现有 `src/orchard/ingest/`**。

   spec 所有新 ingestion 文件（`process-processor.py`, `contract-extractor.py`, `community-processor.py`）都放在 `src/orchard/ingestion/` 下。但 orchard 现有代码使用 `src/orchard/ingest/`（含 `indexstore.py`, `symbolgraph.py`, `swiftinterface.py`）。

   这是冲突 —— 要么新建 `ingestion/` 包并迁移旧代码，要么统一使用 `ingest/`。按 orchard "一个概念一个词"的规范，应保持一致。建议统一用现有 `ingest/` 或将 `ingest/` 重命名为 `ingestion/`。

2. **`schema.py` 已存在**。

   spec 说 "Add `confidence DOUBLE` + `reason STRING` to all rel tables" 但 `graph/schema.py` 已存在且 `Calls`、`BridgesTo`、`ViewTree`、`NavigationFlow` 表已有 `confidence` 列。这是 schema 修改而非新建文件。

3. **`pipeline/runner.py` 将冲突**。

   orchard 已有 `pipeline/runner.py`（`run_ingest_pipeline` 函数，手动顺序执行）。spec 的 Kahn runner 要么替换它（破坏性变更），要么需要不同文件名如 `pipeline/dag_runner.py`。

**包结构一致性**：

orchard 现有子包：`build/`, `derive/`, `graph/`, `handlers/`, `ingest/`, `normalize/`, `pipeline/`, `query/`, `search/`, `validation/`。spec 新增 `incremental/` —— 命名风格一致（形容词/名词，单数形式）。

---

### Q3：P0/P1/P2 优先级分层是否正确？

**P0 -- Architecture Foundation**：合理。
- #1 KnowledgeGraph：重构风险高但确为基础。现有代码直接写 LadybugDB，改为 in-memory KG 是大型重构。
- #2 confidence/reason：schema 基础，低风险，放在最前面正确。
- #3 Pipeline DAG：结构基础，但 orchard 已有 working pipeline。风险中等，应明确替换策略。

**P1 -- Query & Analysis**：部分有问题。
- #4 RRF search：正确。搜索质量提升，非阻塞。
- #5 Process detection：**如果依赖 #8 Communities（P2），则不应在 P1**。GitNexus 中 processes 在 communities 之后。
- #6 Contract extraction：高风险 + 设计细节不足，放在 P1 过早。建议降为 P2 或拆分为 P1（contract schema 设计）+ P2（跨 target 匹配）。

**P2 -- Performance & Clustering**：基本合理。
- #7 Incremental indexing：高风险，需正确的基础设施（KG 的 mutation API #1 完成后方可实施）。P2 合理。
- #8 Leiden communities：低风险，算法独立。但如果 #5 Process 依赖它，则应在 #5 之前。

**Implementation Order 问题**：

```
Phase 5: #5 Process detection
Phase 7: #8 Leiden communities
```

这在 GitNexus 中顺序是反的（DAG: `... → communities → processes`）。如果 orchard 的 process 检测不需要 community 信息，应该**显式说明原因**（例如："iOS 项目中 process 通过 SwiftUI View hierarchy 检测，不依赖 community"）。否则，应调整为：

```
Phase 5: #8 Leiden communities
Phase 6: #5 Process detection (depends on communities)
```

---

### Q4：Python 适配需要哪些与 TypeScript 原版不同的处理？

1. **KnowledgeGraph interface → Python class**。

   TypeScript 用工厂函数 + 闭包隐藏内部实现。Python 更自然的实现是 class + 私有方法（`_add_to_bucket`, `_remove_from_bucket`）。

   TypeScript 的 `ReadonlyMap<>` 约束在 Python 中无编译时等价位。需用文档约定或运行时 `types.MappingProxyType` 保护。

   TypeScript 的 `IterableIterator<T>` 返回类型在 Python 中对应 `Iterator[T]` 或 generator。应明确约定：迭代器是"一次性消耗"还是"可重复使用"（GitNexus 每次调用返回 fresh iterator）。

2. **Pipeline type safety**。

   TypeScript 的 `PipelinePhase<TOutput>` 和 `getPhaseOutput<T>(deps, 'name')` 提供编译时类型安全。Python 的 Protocol 不提供泛型类型检查。建议：
   - 用 `typing.TypeVar` + `Protocol` 标注返回类型（文档用途）
   - `get_phase_output(deps, name, expected_type)` 做运行时类型断言
   - 或直接信任 duck typing（Pythonic 方式）

3. **LadybugDB 批量写入**。

   spec 说 "COPY FROM" —— 需确认 LadybugDB Python SDK 是否支持。GitNexus 用 CSV streaming（`loadGraphToLbug()` → CSV → LadybugDB import）。如果 LadybugDB 不支持 COPY FROM，应改用批量 Cypher CREATE/MERGE 或 CSV import 路径。

4. **Leiden 算法库**。

   使用 `leidenalg`（python-igraph 生态），与 TypeScript 实现 API 不同。需文档化参数选择。

5. **并发模型差异**。

   TypeScript 的 `async/await` 是单线程事件循环（适合 I/O 并行）。Python 的 `asyncio` 也是单线程事件循环但有 GIL 限制。对于 I/O 密集型的 ingestion 阶段（读 IndexStore subprocess），asyncio 适用；对于 CPU 密集型的 community detection，需要 `ProcessPoolExecutor` 或多进程。

---

### Q5：GitNexus 中还有哪些模式应该纳入但被遗漏了？

以下 GitNexus 核心模式在当前 spec 中缺失：

1. **Single CodeRelation Table 设计**。

   GitNexus 所有关系类型共享一张 `CodeRelation` 表（type column 区分），而非每类型一张表。这带来的好处：
   - 统一查询：`MATCH (a)-[r:CodeRelation]-(b) WHERE r.type IN ['CALLS', 'IMPLEMENTS']`
   - 批量操作简化（单次 scan 获取所有边）
   - 新增关系类型无需 DDL 变更
   
   orchard 选择了每类型一张 rel table（`Calls`, `Contains`, `Extends`, …）。这是合理的设计差异，但应**在 spec 中显式文档化此 trade-off**，说明 orchard 选择多表设计的原因（查询性能？类型安全？LadybugDB 最佳实践？）。

2. **SemanticModel 的 Write/Read Phase Contract**。

   GitNexus 的 `SemanticModel` 有严格的阶段性：parse（mutable）→ scope-resolution（mutable）→ finalize（freeze）→ read-only。`runScopeResolution` 通过类型窄化（`MutableSemanticModel` → `SemanticModel`）在 TypeScript 编译时强制此约束。

   对 orchard 来说，in-memory KG（#1）类似 SemanticModel。spec 应定义 KG 的阶段性访问规则：哪些阶段写入 KG？何时 KG 变为只读？是否需要 `freeze()` 方法？

3. **MRO (Method Resolution Order) 阶段**。

   GitNexus 有显式的 MRO 阶段（`mro.ts`），计算 METHOD_OVERRIDES + METHOD_IMPLEMENTS 边。orchard 的 `Implements` 表存储协议遵循/方法覆写，但没有显式的 MRO 计算。Swift/ObjC 的方法分派（protocol extension、class inheritance）需要 MRO 来正确解析调用目标。

   缺失 MRO 直接影响 #5 Process Detection 的质量（调用链不完整）和 #8 Community Detection 的准确性（调用图不完整）。建议新增 P1 或 P2 的 MRO 阶段，或在 Process 设计中说明如何处理缺失 MRO 的调用解析。

4. **Per-Type Relationship Iteration Hot-Path Design**。

   GitNexus 的 `iterRelationshipsByType(type)` 维护 per-type Map 索引以实现 O(type-edges) 遍历（而非 O(total-edges) + filter）。这是 MRO、heritage、社区检测等遍历重关系类型的关键性能优化。spec 的 `rels_by_type: dict` 暗示有此索引，但**未定义内层数据结构**（GitNexus 用 Map<id, Rel> 以支持 O(1) 删除）。

   建议：在 #1 KnowledgeGraph 设计中明确 `rels_by_type` 的内部结构为 `dict[RelationshipType, dict[str, Relationship]]`，并提供 `iter_relationships_by_type(type) -> Iterator[Relationship]` 方法。

5. **Binding Accumulator / Cross-File Type Propagation**。

   GitNexus 的 `crossFile` 阶段在 import 拓扑序中传播类型信息。对 orchard 来说，IndexStore 已包含跨文件引用信息（Occurrence + RefersTo 边），所以这一模式的必要性较低。但跨 target（app target → framework target）的类型传播可能需要类似机制。

6. **Worker Pool + Chunked Parsing**。

   对于 orchard，解析阶段（IndexStore subprocess + SymbolGraph JSON）是 I/O 密集的，现有 asyncio 并发已足够。但对于未来可能的大量 Swift 文件直接解析（tree-sitter），worker pool 模式有价值。暂时合理省略。

---

## 总结建议

### 必须修复（阻塞性）
1. **#5/#8 依赖顺序调整**：将 Communities (#8) 移到 Processes (#5) 之前，或在 spec 中明确说明 orchard 的 process 检测为何不需要 community 信息
2. **`ingestion/` → `ingest/`**：统一使用现有 `ingest/` 目录，或制定迁移计划
3. **文件名改为 snake_case**：`process-processor.py` → `process_processor.py`，`contract-extractor.py` → `contract_extractor.py`，`community-processor.py` → `community_processor.py`
4. **#7 来源标注修正**：增量索引是原创模式，不应标注为 "borrowed from GitNexus"

### 建议修复（质量提升）
5. **#1 KnowledgeGraph API 详述**：补全 mutation/query 方法签名（`add_node`, `remove_node`, `remove_nodes_by_file`, `iter_relationships_by_type` 等）
6. **#2 schema 修改细化**：区分已有 confidence 列的表（仅加 reason）和无 confidence 的表（两列都加），说明与现有 `source`/`provenance` 列的关系
7. **#3 Pipeline 并发策略**：说明新的 DAG runner 是否保留 `asyncio.gather` 的并发执行能力
8. **DAG runner 文件名冲突**：现有 `pipeline/runner.py` 与新 runner 文件冲突，需要区分
9. **COPY FROM 验证**：确认 LadybugDB Python SDK 支持 COPY FROM，或改用 CSV streaming

### 建议新增（长期完善）
10. **Trade-off 文档**：single CodeRelation table vs 多表设计的权衡分析
11. **KG 阶段性访问规则**：定义 in-memory KG 的 write/read phase contract
12. **MRO 阶段评估**：评估是否需要独立的 MRO 计算阶段以提升 Process/Community 质量
13. **#6 Contract Extractor 设计细化**：降低风险后再提升优先级，或先做 contract schema 设计（P1）再实现跨 target 匹配（P2）
