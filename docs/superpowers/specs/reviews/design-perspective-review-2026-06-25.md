# Design Perspective Review -- Orchard sourcekit-lsp Pattern Optimizations

**Date**: 2026-06-25
**Reviewer**: Architecture design agent
**Spec**: `2026-06-25-orchard-sourcekit-lsp-optimizations.md`

## 总分：3.5/5

## 各维度评分

| 维度 | 分数 | 关键发现 |
|---|---|---|
| Pattern Faithfulness | 3.5/5 | 核心翻译正确，但 Primary Definition 的排序策略和降级回退与原始行为不一致 |
| Naming Consistency | 3.0/5 | P1/P2 中同一概念用了两种命名风格；heading 用 camelCase 但 Python 代码用 snake_case |
| Priority Ordering | 3.0/5 | P1 #3 和 P2 #5 是同一 feature 拆成了两个优先级，应合并 |
| Python/Ladybug Adaptation | 3.5/5 | 整体适配合理，但 CrossLanguageName 依赖尚未实现的 USR 关联；inMemoryModifiedFiles 的关联值建模未说明 |

## 详细分析

### Q1：Pattern 翻译是否忠实于 sourcekit-lsp 原始设计？有无误解？

**1. containerNamesCache (P0) -- 忠实度：高**

源实现（CheckedIndex.swift:94）是 `private var containerNamesCache: [String: [String]] = [:]`，USR -> 容器名链缓存，per-request 生命周期。spec 的 `container_names_cache: dict[str, list[str]]` 正确翻译了这个模式。

Extension 处理：源实现通过 `extendedBy` role 查找被扩展类型后再递归。spec 说"follow Extends edge" -- Ladybug 的 `Extends` 边正是 `extendedBy` 的对应物（参见 `identity.py:98` 的 `_INDEXSTORE_REL_TO_TABLE` 映射）。处理逻辑正确。

一处细微差异：源实现缓存的是 container USR -> 名字链，因为在 Occurrence 层面可以直接拿到 `containerSymbol.name`。Ladybug 版本需要从 Symbol 节点的 `name` 属性获取，多一次属性读取，但不影响正确性。

**2. CrossLanguageName (P0) -- 忠实度：中高**

源实现（Rename.swift:52）是 struct，含 `clangName: String?`、`swiftName: String?`、`definitionLanguage: Language`，以及 computed `definitionName`。spec 翻译为 dataclass，字段和语义一致。

差异一：源实现的 `Language` 是 enum（`.c`, `.cpp`, `.objective_c`, `.objective_cpp`, `.swift`），spec 用 `str`。这对于 Python 是务实的简化，但应明确约束值为 `'swift'` / `'objc'` / `'c'` / `'cpp'`（与 orchard 现有 Symbol.language 值保持一致）。

差异二：spec 说 ObjC -> Swift 映射语法为 `-[Class method:]` <-> `Class.method()`，但源实现中 CrossLanguageName 本身**不做**名字翻译 -- 它只是存储两种语言的名字。名字翻译由 `NameTranslatorService` protocol（Rename.swift:83）完成，且需要 `SymbolLocation` + `DocumentSnapshot` + async。spec 把这部分简化了，CrossLanguageName 只是一个数据容器，这一点 spec 理解正确。

**风险**：当前 `bridge.py` 只做了 name-match 策略（confidence 0.70），USR correlation 标为 "deferred to M4"。在没有可靠 USR 关联的情况下往 BridgesTo edge 写上 CrossLanguageName，可能为不匹配的符号写入错误的名字。建议在 M4 USR correlation 完成后再接入 CrossLanguageName 写入。

**3. CheckedIndex Freshness / IndexCheckLevel (P1) -- 忠实度：高**

源实现（CheckedIndex.swift:31-48）的 `IndexCheckLevel` enum 有三个 case：`deletedFiles`、`modifiedFiles`、`inMemoryModifiedFiles(any InMemoryDocumentManager)`，spec 准确捕捉了三层策略和 `IndexOutOfDateChecker` 的 modTime cache 设计。

但 `inMemoryModifiedFiles` 携带关联值 `InMemoryDocumentManager`，这是一个 protocol，提供 `fileHasInMemoryModifications(_:)` 方法。spec 没有说明在 Python 中如何建模这个关联值 -- 是作为 checker 的构造函数参数，还是作为 enum 的附加字段？这会影响 API 设计。

**4. transitiveSubtypeClosure (P1) -- 忠实度：N/A（原创模式）**

sourcekit-lsp 没有这个名字的函数。源实现通过 IndexStoreDB 的 `occurrences(relatedToUSR:roles:)` 等原生支持获取子类型，不需要手动图遍历。orchard 是基于 Ladybug 图数据库的，所以需要显式遍历 `Inherits` + `Implements` 边。

spec 的设计（`_subtype_closure(conn, usr) -> set[str]`）正确。但需注意：`Implements` 在 orchard 中对应的是 protocol 方法覆写（`identity.py:95` map `overrideOf` -> `Implements`），而非类型级别的协议遵循。类型级别的协议遵循应走 `ConformsTo` 边。但 spec 说的是 `Implements:FROM` 遍历子类/实现者方向，这可能是把 `Inherits` 和 `Implements` 的 FROM 方向都理解为"谁继承/实现了当前 USR"。需要确认 `Implements` 的语义在 Ladybug schema 中是否包含协议遵循关系。

**5. Three-Level Freshness (P2) -- 忠实度：高，但与 P1 重复**

三个 level 的语义与源实现完全对应。但这是 P1 #3 的**同一功能** -- P1 引入 `IndexCheckLevel` 枚举和 `IndexOutOfDateChecker`，P2 再次描述"replace binary fresh/stale with IndexCheckLevel"。这两个条目的区别不清晰，应合并。

**6. Primary Definition (P2) -- 忠实度：中**

源实现（CheckedIndex.swift:238-244）的逻辑是：
1. 先查 `.definition` 角色的 occurrences
2. 如果为空，降级到 `.declaration` 角色的 occurrences  
3. 对结果 `.sorted()` 取 `.first`（按 location：文件路径 + 行号 + 列号排序）

spec 的描述是"query definitions sorted by file_path, return first"：
- **遗漏了 declaration 降级回退**：源实现中，很多符号（如 C++ 前向声明、protocol 声明）只有 declaration 没有 definition。缺失这个回退会导致查找失败。
- **排序维度不同**：源实现按完整的 SymbolLocation 排序（文件 + 行 + 列），spec 只按 file_path 排序。对于同一文件中有多个同名符号的情况，file_path 排序是不确定的，而源实现的行列号排序保证确定性。
- 源实现返回的是 `SymbolOccurrence?`（可选值），spec 的函数签名未明确失败行为。

建议：要么完整实现 `definition -> declaration -> None` 降级链 + (file_path, line, column) 三元组排序，要么直接说明这是"简化版确定性选择"，并注明与源实现的差异。

### Q2：提议的名称是否符合 orchard 现有命名规范？

**符合规范的命名：**

| 提议名称 | 约定 | 符合？ |
|---|---|---|
| `container_names_cache` (变量) | snake_case | 是 -- 与 `_perf_probes`、`make_symbol_id` 一致 |
| `CrossLanguageName` (dataclass) | PascalCase | 是 -- 与 `GraphFreshness`、`ImpactRequest` 一致 |
| `IndexCheckLevel` (enum 类名) | PascalCase | 是 |
| `IndexOutOfDateChecker` (类名) | PascalCase | 是，但名字偏长 -- orchard 中类名通常 1-2 个单词 |
| `primary_definition_usr` (函数) | snake_case | 是 |
| `_subtype_closure` (函数) | snake_case + `_` 前缀 | 是 -- 与 `_risk_level` 内部函数一致 |

**不符合规范的命名：**

1. **P1 heading "transitiveSubtypeClosure"** 用了 camelCase，但 Python 代码中实际函数名是 `_subtype_closure`（snake_case）。heading 应与代码名保持一致：`transitive_subtype_closure`。

2. **P1 enum 成员 "DELETED_FILES | MODIFIED_FILES | IN_MEMORY_MODIFIED"** 使用了不统一的风格：
   - `DELETED_FILES` / `MODIFIED_FILES` 是全大写 + 截断（缺少 "FILES" 后缀的前两个，第三个有）
   - P2 中同样的枚举值写为 `deleted_files | modified_files | in_memory_modified_files`（全小写）
   - 源实现：`deletedFiles | modifiedFiles | inMemoryModifiedFiles`（camelCase）
   - Python 惯例：enum 成员用 `UPPER_CASE`（PEP 8）。建议统一为 `DELETED_FILES`、`MODIFIED_FILES`、`IN_MEMORY_MODIFIED_FILES`（注意第三个要补全 `FILES`）。

3. **P1 #3 heading "CheckedIndex Freshness Filter"** -- `CheckedIndex` 是 sourcekit-lsp 的类名，orchard 中没有同名的类。建议改为 "Occurrence Freshness Filter" 或 "SymbolLocation Freshness Check"，避免将源实现的类名直接当功能名使用。

### Q3：优先级排序（P0/P1/P2）是否正确？

**建议调整：**

| 条目 | 当前优先级 | 建议优先级 | 理由 |
|---|---|---|---|
| #1 containerNamesCache | P0 | **P0** 保持 | 纯性能优化，直接影响每次查询的 Cypher 往返次数，收益明确 |
| #2 CrossLanguageName | P0 | **P1** | 当前 `bridge.py` 的 USR correlation 尚未实现（M4），CrossLanguageName 缺少可靠的数据基础。降低一级，等 M4 完成后提升 |
| #3 IndexCheckLevel + IndexOutOfDateChecker | P1 | **P0** | 这是**正确性**问题而非性能问题 -- 没有 freshness 过滤会返回已删除/修改文件的过时结果 |
| #4 transitiveSubtypeClosure | P1 | **P1** 保持 | 提升影响分析精度，但不阻塞基本功能 |
| #5 Three-Level Freshness | P2 | **合并到 #3** | 与 #3 是同一功能的两个描述，不应独立存在 |
| #6 Primary Definition | P2 | **P2** 保持 | 确定性选择是 polish，非阻塞 |

**关键调整理由：**

- `CrossLanguageName` 降级：当前 bridge recovery 只做了 name-match（conf 0.70），USR correlation 被推迟到 M4。在可靠的符号关联建立之前，CrossLanguageName 中存储的名字可能是错的。应推迟到与 M4 一起交付。
- `IndexCheckLevel + IndexOutOfDateChecker` 提升：源实现中，`forEachSymbolOccurrence` 等方法**每一步**都调用 `checker.isUpToDate(occurrence.location)` 过滤，没有这个检查就返回过时数据。这是功能正确性的基础保障，不应放在 P1。

### Q4：Python/Ladybug 环境需要哪些与 Swift/IndexStoreDB 不同的适配？

**1. containerNamesCache -- 遍历成本差异**

IndexStoreDB 的 `primaryDefinitionOrDeclarationOccurrence(ofUSR:)` 是 O(1) 查找，因为它维护了内部索引。Ladybug 需要通过 Cypher 查询遍历 `Contains` 边，每条边都是一次图遍历。

建议：在 `owner_of()` 中缓存的不只是最终的容器名链，还包括中间查询结果（如已解析的 container USR），以减少重复的图查询。可考虑一次 Cypher 查询带回整个 ancestor chain 而非逐层查询。

**2. CrossLanguageName -- 异步翻译不适用**

源实现的 `NameTranslatorService` 是 async/await 的，因为它需要访问源文件快照。在 orchard 的离线批处理场景中，没有"当前编辑器快照"概念，所有名字翻译必须仅依赖编译产物的静态数据。

spec 中 `-[Class method:]` <-> `Class.method()` 的字符串映射是可行的离线策略，但应明确这只是语法层面的 name mapping，不等同于 sourcekit-lsp 的语义级翻译。

**3. IndexCheckLevel.inMemoryModifiedFiles -- 关联值建模**

Swift 的 enum with associated value 在 Python 中没有直接对应。源实现中 `inMemoryModifiedFiles(any InMemoryDocumentManager)` 携带了编辑器文档管理器引用。在 orchard 的离线批处理场景中，没有"编辑器未保存修改"的概念。

建议：在 orchard 中，`inMemoryModifiedFiles` 可以直接作为一个不带关联值的枚举成员，因为 orchard 只做离线索引。如果未来要支持 LSP 集成，可以改为将 InMemoryDocumentManager 作为 IndexOutOfDateChecker 的可选构造参数。

**4. transitiveSubtypeClosure -- 递归性能边界**

IndexStoreDB 内部维护了类型层次的预计算缓存。Ladybug 的递归图遍历在大型类型层次（如 UIKit 中 UIResponder -> UIView -> UIControl -> UIButton）中性能可预测，但在极端情况（如 clang 模块，llvm namespace 含数千类型）下递归可能产生大量 Cypher 查询。

建议：增加深度限制参数（如 max_depth=20），并在函数文档中说明性能特征。

**5. Primary Definition -- 排序确定性来源不同**

IndexStoreDB 的 `SymbolOccurrence` 包含行号和列号，排序是全序的。Ladybug 的 Symbol 节点目前不存储行号/列号（查看 `upsert_symbols` 中的字段列表：usr, precise_id, name, language, kind, module, target_id, file_path, signature, container_usr, access_level）。仅有 `file_path` 排序，在多个定义位于同一文件时结果不确定。

建议：要么扩展 Symbol schema 增加 `line` / `column` 属性，要么在文件路径排序后增加二级排序键（如 usr 字母序）以保证确定性。同时需要补充 declaration 降级回退逻辑。

## 修复建议

### 必须修复（阻塞合入）

1. **合并 P1 #3 和 P2 #5** -- 它们是同一功能的两个描述。保留 P1 版本的 `IndexCheckLevel` + `IndexOutOfDateChecker`，删除 P2 的 "Three-Level Freshness" 条目，或将其改为描述 MCP 层如何暴露 freshness level 给调用方。

2. **统一命名风格** -- enum 成员统一为 `UPPER_CASE`：`DELETED_FILES`、`MODIFIED_FILES`、`IN_MEMORY_MODIFIED_FILES`。heading "transitiveSubtypeClosure" 改为 "transitive_subtype_closure"。heading "CheckedIndex Freshness Filter" 改为 "Occurrence Freshness Filter" 或直接省略 "CheckedIndex" 前缀。

3. **CrossLanguageName 降级到 P1** -- 明确依赖 M4 USR correlation，在 spec 中标注 `depends_on: M4_USR_CORRELATION`。

### 建议修复（提升质量）

4. **Primary Definition 补全降级链** -- 增加 "definition 为空时降级到 declaration occurrences" 的步骤，避免 protocol/forward-decl 符号查找失败。

5. **Primary Definition 排序键扩展** -- 明确排序键为 `(file_path, usr)` 或 `(file_path, line, column)`（需要扩展 Symbol schema），并注明与源实现 `(filePath, line, column)` 排序的差异。

6. **IndexCheckLevel 提升为 P0** -- freshness 过滤是功能正确性的基础，应在 CrossLanguageName 之前交付。

7. **transitiveSubtypeClosure 增加深度限制** -- 标注 `Implements` 边在 orchard 中的精确语义（是 protocol 遵循还是方法覆写），防止理解偏差。

## 结论

这 6 个模式的翻译整体质量良好，核心架构决策（cache 放在 GraphLookup、freshness 在 validation 模块、subtype closure 在 handlers/impact.py）符合 orchard 现有模块职责划分。主要问题是：

1. **P1 #3 和 P2 #5 的重复** -- 这是 spec 写作问题，不是设计问题，容易修复。
2. **CrossLanguageName 的前置依赖未满足** -- M4 USR correlation 是可靠 cross-language name 的前提，当前 P0 排期过早。
3. **Primary Definition 的排序和降级逻辑需要精细化** -- 当前设计过于简化，可能在边界情况产生不确定结果。

修复这 3 个问题后，spec 达到可进入实现规划阶段的标准。
