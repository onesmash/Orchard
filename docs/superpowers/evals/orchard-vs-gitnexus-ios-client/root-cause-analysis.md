# orchard find_callers 空结果根因分析

## Phase 1: 问题复现

### 症状
`orchard find_callers` 对多数符号返回空结果，尽管数据库中有 615K CALL 边。

### 成功 vs 失败对比

| 查询 USR | 类型 | 结果 |
|----------|------|------|
| `c:objc(cs)ZPJoinConfHelper` | class | **0 callers** |
| `c:objc(cs)ZPConfUIContainer` | class | **0 callers** |
| `c:objc(cs)ZMMeetingSceneDelegate` | class | **0 callers** |
| `c:objc(cs)ZPAppDelegate` | class | **0 callers/callees** |
| `c:objc(cs)ZPJoinConfHelper(im)acceptVideoCall:` | instance method | **8 callers** ✅ |
| `c:objc(cs)ZPConfUIContainer(im)createZoomController` | instance method | **2 callers** ✅ |
| `c:objc(cs)ZPConfUIContainer(im)showZoomController` | instance method | **2 callers** ✅ |
| `c:objc(cs)ZMNotiManager(im)_handleMeetingPushNoti:forType:` | instance method | **4 callers** ✅ |
| `c:objc(cs)ZPJoinConfHelper(im)acceptVideoCall:` | instance method | **21 callees** ✅ |
| `c:objc(cs)ZMPTViewControllerHelper(im)tryAutoLoginWhenAppLaunched` | instance method | **1 caller + 89 callees** ✅ |
| `c:objc(cs)ZMMeetingSceneDelegate(im)scene:willConnectToSession:options:` | delegate method | **0 callers** |

## Phase 2: 根因分析

### 根因 1: Class vs Method 查询粒度 (主要误判来源)

**问题**: 比较评测时，`find_callers` 使用的是 **class 级别的 USR**，而非 method 级别的 USR。

**原因**: IndexStore 追踪的是 **方法调用** (method calls)，不是"类引用"。`c:objc(cs)ZPJoinConfHelper` 是类符号，没有代码直接"调用"一个类——代码调用的是 `[[ZPJoinConfHelper sharedInstance] acceptVideoCall:]`，这是方法调用。

**证据**: 
- `ZPJoinConfHelper` class → 0 callers
- `ZPJoinConfHelper(im)acceptVideoCall:` → **8 callers** (hudWasHidden:, showInviteAlert:, meetingAcceptCall, presentInviteAlert:...)
- `ZPJoinConfHelper(im)acceptVideoCall:` → **21 callees** (createInvocation:, invokeAcceptCall:template:, leaveConferenceWith:...)

**影响范围**: S2 评测结果被错误降低。果园的调用图对 method 级别查询是正常工作的。

### 根因 2: 搜索不返回类的方法

**问题**: `orchard search --name "ZPJoinConfHelper"` 仅返回 1 条结果（类本身），不返回其方法。

**原因**: 搜索只做名称匹配。方法的 USR 中包含类名（如 `ZPJoinConfHelper(im)acceptVideoCall:`），但名称字段不包含类名前缀。

**影响范围**: 用户必须先知道方法名，才能找到方法的 USR，再查询 callers。缺少 "显示此类的所有方法及其调用者" 的能力。

### 根因 3: 系统 framework 回调无调用者（非 bug）

**问题**: `application:didFinishLaunchingWithOptions:` 和 `scene:willConnectToSession:options:` 等方法有 0 callers。

**原因**: 这些是 UIKit 系统回调——调用者在 iOS 系统框架内部，不在 IndexStore 的索引范围内。这是 IndexStore 的固有边界，不是 bug。

**解决方案**: 从 callees 方向反向追溯：`didFinishLaunching` → `tryAutoLoginWhenAppLaunched` 这个关系已被确认存在（`tryAutoLoginWhenAppLaunched` 有 1 个 caller: `application:didFinishLaunchingWithOptions:`）。

### 根因 4: C++ operator 噪音淹没 callee 列表

**问题**: `tryAutoLoginWhenAppLaunched` 有 89 个 callees，但约 80 个是 `operator<<`、`operator&` 等 C++ 流/日志操作符。

**原因**: C++ 的 `<<` 操作符每次使用都被 IndexStore 记录为一次调用。对包含大量日志的 ObjC++ 方法，噪音比 > 10:1。

**影响范围**: 用户无法快速从 callee 列表中找到下一个业务逻辑跳转点。

### 根因 5: iOSLogin 模块部分覆盖

**问题**: `ZMAppLoginHelper` 在 orchard 中搜不到，但 `LoginServiceProtocol` 和 `ZMPTViewControllerHelper.tryAutoLoginWhenAppLaunched` 存在。

**证据**:
- `orchard search --name "ZMAppLoginHelper"` → 0 结果
- `orchard search --name "ZMPTViewControllerHelper"` → 不存在（但 tryAutoLoginWhenAppLaunched 作为方法存在）
- `orchard search --name "LoginServiceProtocol"` → 1 结果 (iOSServiceManager 模块)
- `orchard stats` 显示 234K 符号, IndexStore 源

**可能原因**: iOSLogin 模块可能编译为 Swift module 但 ObjC 接口未被 IndexStore 完整记录；或 IndexStore 来自不包含 iOSLogin target 的构建配置。

## Phase 3: 对评测分数的影响

### 需要修正的评测

| 场景 | 原评分 | 修正评分 | 原因 |
|------|--------|----------|------|
| S2 orchard task_completion | 1 (partial) | **2 (success)** | method 级别查询: acceptVideoCall: 有 8 callers + 21 callees，可追踪完整 join 流程 |
| S2 orchard semantic | 1 | **2** | 调用关系完整准确，包括 C++↔ObjC 跨语言 |
| S2 orchard explainability | 1 | **2** | 有 depth/reason/owner 标注 |
| S7 orchard task_completion | 0 (fail) | **1 (partial)** | didFinishLaunching → tryAutoLoginWhenAppLaunched 追踪到，但 callee 噪音大 |

### 修正后总分

| | orchard 原总分 | orchard 修正后 | GitNexus 原总分 |
|--|---------------|----------------|----------------|
| | 23/64 | **29/64** | 36/64 |

## Phase 4: 行动项

### orchard 侧
1. **P0**: `find_callers` 对 class USR 应自动展开为所有 method 的 callers 聚合
2. **P0**: `search` 应支持 `--owner` / `--class` 参数，返回指定类的所有方法
3. **P1**: callee 列表需要过滤 C++ operator 噪音（默认隐藏 `operator<<` 等）
4. **P1**: 调查 iOSLogin 模块 IndexStore 覆盖问题
5. **P2**: 系统回调方法应标注 "framework boundary — use reverse tracing"

### 评测方法论侧
1. 后续评测必须先获取 method 级别 USR 再查询 callers
2. 需要区分 "查询无结果" 和 "框架边界" 两种情况
3. callee 噪音大应列为 explainability 扣分而非 task_completion 扣分
