# orchard 查询层优化 — 基于 root-cause-analysis 的设计方案

## Summary

基于 orchard-vs-gitnexus-ios-client 比较评测的 5 个根因发现，优化 orchard CLI 查询层和搜索层，修复 find_callers class→method 自动展开、search 方法发现、C++ operator 噪音过滤、framework 边界标注和 iOSLogin 模块覆盖诊断。

## Design Goals

1. `find_callers` 对 class USR 自动展开为所有 method callers 聚合
2. `search` 支持 `--class` 参数返回指定类的所有方法
3. `find_callees` 默认过滤 C++ operator 噪音
4. `find_callers` 结果为 0 时自动标注是否为 framework 边界
5. 新增 `orchard audit` 命令诊断 IndexStore 模块覆盖

## 方案设计

### 方案 1: class→method 自动展开 (P0)

**问题**: `find_callers --usr c:objc(cs)ZPJoinConfHelper` 返回 0 callers，因为 class 级别没有 Calls 边。实际 8 个 method 上的 callers 存在但不能被发现。

**目标行为**: 当 `find_callers` 收到 class/struct/enum USR 时，自动查找该类型的所有 (im) instance method + (cm) class method，聚合 callers，按方法分组返回。

**实现路径**:

1. **新增 `GraphLookup.children_of(usr)` / `methods_of(usr)`** (`src/orchard/query/lookup.py`):
   ```python
   def methods_of(self, usr: str, target_id: str = "") -> list[dict]:
       """Return all methods (im+cm) contained in a class/struct/enum."""
       sym_id = make_symbol_id(target_id or self._infer_target(usr), usr)
       # Via Contains edges: class -[:Contains]-> method
       results = self.conn.execute("""
           MATCH (parent:Symbol {id: $id})-[:Contains]->(child:Symbol)
           WHERE child.kind IN ['method', 'instanceMethod', 'classMethod']
           RETURN DISTINCT child.usr, child.name, child.kind, child.language
           ORDER BY child.name
       """, {"id": sym_id})
       return list(results)
   ```

2. **修改 `find_callers` handler** (`src/orchard/handlers/callers.py`):
   ```python
   def find_callers(conn, req: CallerRequest) -> BaseToolResponse:
       g = GraphLookup(conn)
       sym = g.symbol(req.usr, req.target_id or "")
       if sym and sym.get("kind") in ("class", "struct", "enum", "protocol"):
           # Auto-expand: aggregate callers of all methods
           methods = g.methods_of(req.usr, req.target_id or "")
           all_callers = []
           for m in methods:
               callers = g.callers_of(m["usr"], req.target_id or "")
               for c in callers:
                   c["via_method"] = m["name"]
                   c["depth"] = 1
               all_callers.extend(callers)
           # Deduplicate by caller USR
           seen = set()
           unique = []
           for c in all_callers:
               if c["usr"] not in seen:
                   seen.add(c["usr"])
                   unique.append(c)
           return BaseToolResponse(data=unique, ...)
       # Existing single-symbol path
       data = g.callers_of(req.usr, req.target_id or "")
       ...
   ```

3. **`find_callees` 展开策略**: 与 `find_callers` 不同，callee 展开使用 **group-by-callee** 而非 dedup-by-USR。原因：不同 method 可能调用相同的 callee（如多个 method 都调用 `shareInstance`），group-by 能显示 `shareInstance 被 5 个 method 调用` 的信息。对 15+ method 的 class，需限制展开的 method 数量（默认 top-50）防止输出过多。

**CLI 签名**: 不变。用户行为透明升级。

**验证**:
```bash
# Before: 0 callers
orchard find_callers --usr "c:objc(cs)ZPJoinConfHelper" --target Zoom

# After: ~8 callers, grouped by method, with via_method annotation
orchard find_callers --usr "c:objc(cs)ZPJoinConfHelper" --target Zoom
# Expected: hudWasHidden:(via acceptVideoCall:), showInviteAlert:(via acceptVideoCall:), ...
```

### 方案 2: search --class 参数 (P0)

**问题**: `orchard search --name "ZPJoinConfHelper"` 只返回类本身(1 条)，不返回其方法。用户必须先知道方法名才能查询。

**目标行为**: `orchard search --class <ClassName>` 先查找类，再通过 Contains 边或 container_usr 返回该类的所有方法。

**实现路径**:

1. **修改 `cmd_search()`** (`src/orchard/cli.py:278`):
   - 新增 `--class` / `-c` 参数
   - 解析流程:
     a. 先通过名称搜索找到匹配的 class/struct/enum
     b. 对每个匹配的类，调用 `methods_of()` 获取所有方法
     c. 返回时在方法名上标注所属类

2. **新增 `GraphLookup.search_methods_of_class(name_pattern, target)`**:
   组合搜索 + 方法展开的快捷路径。

**CLI 签名**:
```
orchard search --class <ClassName> [--target <Module>] [--kind method] [--limit N]
```

**输出示例**:
```json
{
  "owner": {"name": "ZPJoinConfHelper", "usr": "c:objc(cs)ZPJoinConfHelper"},
  "methods": [
    {"name": "acceptVideoCall:", "usr": "c:objc(cs)ZPJoinConfHelper(im)acceptVideoCall:", "kind": "method"},
    {"name": "createInvocation:", "usr": "c:objc(cs)ZPJoinConfHelper(im)createInvocation:", "kind": "method"},
    {"name": "executeInvocation", "usr": "c:objc(cs)ZPJoinConfHelper(im)executeInvocation", "kind": "method"},
    ...
  ]
}
```

**验证**:
```bash
orchard search --class ZPJoinConfHelper --target Zoom
# Expected: 15 methods listed with USRs
```

### 方案 3: C++ operator 噪音过滤 (P1)

**问题**: `find_callees` 返回结果的 80%+ 是 `operator<<`, `operator&`, `LogMessage` 等 C++ 日志/流操作，淹没有意义的业务调用。

**目标行为**: `find_callees` 默认过滤已知噪音符号，`--include-noise` 恢复完整列表。

**实现路径**:

1. **新增噪音过滤器** (`src/orchard/query/noise_filter.py`):
   ```python
   # C++ noise operators: prefix-matched to avoid false positives
   # startswith('operator') avoids matching 'stream'/'str' in uploadStream/destroy/restore
   CPP_NOISE_PREFIXES = [
       "operator<<", "operator>>", "operator&", "operator->",
       "operator()", "operator[]", "operator new", "operator delete",
       "operator bool", "operator=", "operator+", "operator-",
       "operator*", "operator/", "operator%", "operator^",
       "operator|", "operator~", "operator!", "operator<",
       "operator>", "operator,", "operator->*", "operator<=>",
   ]
   # Exact-match noise: logging/stream helpers
   CPP_NOISE_EXACT = {
       "GetMinLogLevel", "LogMessage", "LogMessageVoidify",
       "defaultCenter", "postNotificationName:object:",
       "StringPiece", "basic_stringstream", "NSLog",
       "c_str", "str", "stream",  # std::string / std::stringstream accessors
   }

   def is_noise(name: str) -> bool:
       if name in CPP_NOISE_EXACT:
           return True
       for prefix in CPP_NOISE_PREFIXES:
           if name.startswith(prefix):
               return True
       return False

   def filter_noise(callees: list[dict]) -> tuple[list[dict], int]:
       filtered = [c for c in callees if not is_noise(c.get("name", ""))]
       removed = len(callees) - len(filtered)
       return filtered, removed
   ```

2. **修改 `cmd_find_callees()`** (`src/orchard/cli.py:101`):
   - 新增 `--include-noise` flag (默认 False)
   - 在返回前应用 `filter_noise()`
   - 在 response metadata 中标注 `noise_removed: N`

3. **同样应用于 `find_callers`**: callers 中的噪音调用者也过滤。

**CLI 签名**:
```
orchard find_callees --usr <USR> [--target <Module>] [--include-noise]
```

**输出示例**:
```json
{
  "data": [/* business-logic callees only */],
  "metadata": {"noise_removed": 80, "total_raw": 89}
}
```

**验证**: 使用 `tryAutoLoginWhenAppLaunched` (之前 89 callees, 80 噪音):
```bash
# Default: ~9 business-logic callees
orchard find_callees --usr "c:objc(cs)ZMPTViewControllerHelper(im)tryAutoLoginWhenAppLaunched" --target Zoom

# With noise: all 89 callees
orchard find_callees --usr "..." --target Zoom --include-noise
```

**权衡**: 噪音过滤可能导致极少数情况下的误判（把非噪音 C++ operator 过滤掉）。`--include-noise` 提供安全网。

### 方案 4: Framework 边界标注 (P1)

**问题**: `application:didFinishLaunchingWithOptions:` 等 delegate 方法 find_callers 返回 0，但这是 framework 调用的正常现象。用户需要知道"不是 bug，是边界"。

**目标行为**: 当 find_callers 返回 0 时，检查是否是已知的 framework delegate/callback 模式，如是则自动标注。

**实现路径**:

1. **新增 framework 边界检测** (`src/orchard/query/lookup.py`):
   ```python
   FRAMEWORK_CALLBACK_PATTERNS = [
       # UIApplicationDelegate
       r"application:", r"applicationWill", r"applicationDid",
       # UISceneDelegate / UIWindowSceneDelegate
       r"scene:", r"sceneWill", r"sceneDid",
       # UIViewController lifecycle
       r"viewDidLoad", r"viewWillAppear:", r"viewDidAppear:",
       r"viewWillDisappear:", r"viewDidDisappear:",
       r"viewWillLayoutSubviews", r"viewDidLayoutSubviews",
       # NSObject / NSApplication
       r"awakeFromNib", r"loadView",
       # UITableView / UICollectionView data sources
       r"numberOfSections", r"numberOfRows", r"cellForRow",
       r"numberOfItems", r"cellForItem",
   ]

   def is_framework_callback(name: str) -> bool:
       import re
       for pattern in FRAMEWORK_CALLBACK_PATTERNS:
           if re.search(pattern, name):
               return True
       return False
   ```

2. **修改 `find_callers` 响应**: 当 `data == []` 时，检查符号名并添加 open_gap:
   ```json
   {
     "data": [],
     "open_gaps": [
       "No callers found — likely called by system framework (UIKit/AppKit). Use reverse tracing via find_callees."
     ]
   }
   ```

**CLI 签名**: 不变。响应注解自动添加。

### 方案 5: orchard audit 命令 (P1)

**问题**: iOSLogin 模块的 ObjC 类（ZMAppLoginHelper 等）不在 orchard 图中，但同项目的其他模块正常。需要工具诊断 IndexStore 覆盖状况。

**目标行为**: `orchard audit --project-dir <path>` 分析 graph.db，对比 Xcode workspace 的 targets，报告覆盖率和缺失模块。

**实现路径**:

1. **新增 `orchard audit` 命令** (`src/orchard/cli.py`):
   ```python
   def cmd_audit(args):
       """Diagnose IndexStore module coverage."""
       # Parse project dir
       project_dir = args.project_dir or "."
       # 1. Read graph.db stats: modules, targets, symbol counts
       # 2. Get Xcode workspace targets (via xcodebuild -list 或 .xcworkspace)
       # 3. Compare and report:
       #    - covered targets with symbol counts
       #    - targets with zero symbols (potential gap)
       #    - modules in graph not matching any known target
       g = GraphLookup(conn)
       # ...
   ```

2. **新增 `GraphLookup.module_stats()`**: 返回每个 module 的符号计数（按 kind 分组）。

3. **报告格式**:
   ```
   Target               | Symbols | Methods | Classes | Protocols
   ---------------------|---------|---------|---------|----------
   Zoom                 | 180,234 | 45,000  | 12,340  | 890
   iOSServiceManager    | 23,456  | 5,678   | 1,234   | 45
   iOSLogin             | 345     | 89      | 12      | 8   ← UNEXPECTED GAP
   ...
   ```

**CLI 签名**:
```
orchard audit [--project-dir <path>] [--db <path>] [--format table|json]
```

**验证**:
```bash
cd /Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client
orchard audit --project-dir . --format table
# Expected: shows iOSLogin with anomalously low symbol count
```

## CLI 签名变更汇总

| 命令 | 变更类型 | 说明 |
|------|----------|------|
| `find_callers` | 行为升级 | class USR 自动展开为 method callers 聚合，输出新增 `via_method` 字段 |
| `find_callees` | 行为升级 + 新 flag | class USR 自动展开 + `--include-noise` 关闭噪音过滤 |
| `search` | 新 flag | `--class <ClassName>` 查找并返回类的所有方法 |
| `audit` | **新命令** | 诊断 IndexStore 模块覆盖率和缺失 gap |

## 实现优先级

| # | 方案 | 优先级 | 预计改动文件 | 风险 |
|---|------|--------|-------------|------|
| 1 | class→method 展开 | **P0** | lookup.py, handlers/callers.py, handlers/callees.py | 低 — 仅新增逻辑路径，不改变现有行为 |
| 2 | search --class | **P0** | cli.py, lookup.py, server.py | 低 — 新增 flag，不影响现有搜索 |
| 3 | C++ operator 噪音过滤 | P1 | cli.py, query/noise_filter.py(new), server.py | 中 — 默认行为变更，需提供 --include-noise 逃生门 |
| 4 | Framework 边界标注 | P1 | lookup.py | 极低 — 仅新增 open_gap 文本 |
| 5 | orchard audit | P1 | cli.py, query/lookup.py | 低 — 新命令，只读操作 |
| 6 | 多 target IndexStore 合并 | P1 | ingest 流程 | 中 — 影响 ingest 逻辑，需改变 IndexStore discovery |

## 实施顺序

1. 先做 P0 (方案 1 + 2)，因为这两个直接影响 find_callers 的可用性
2. P1 中先做噪音过滤 (方案 3)，因为它在 callee 查询中影响最大
3. framework 标注 (方案 4) 和 audit (方案 5) 最后做，因为它们是辅助增强

## 非目标

- 不引入 breaking change — 所有现有 CLI 命令签名保持不变
- 不修改 DB schema — 查询层优化，不涉及数据层改动
- ~~不改变 IndexStore ingest 流程~~ → **已更新**: iOSLogin 根因分析确认需要多 target IndexStore 合并支持。新增方案 6：`orchard ingest --all-targets` 从 `.xcworkspace` 自动发现所有 scheme 并合并 IndexStore
