# Design Perspective Review — Orchard Query Optimization from Root Cause Analysis

**Date**: 2026-06-26
**Perspective**: design
**Reviewer**: Design / architecture review
**Spec**: `docs/superpowers/specs/2026-06-26-orchard-query-optimization-from-root-cause-analysis.md`
**Root Cause Analysis**: `docs/superpowers/evals/orchard-vs-gitnexus-ios-client/root-cause-analysis.md`
**Verdict**: All 5 solutions correctly target their root causes. The scope is well-constrained, and the implementation order is sound. Two ISSUE-level gaps need attention before implementation: (1) the auto-expand dedup strategy loses call-site fidelity, and (2) the iOSLogin root cause demands ingest-flow changes that the spec explicitly excludes.

---

## Q1: Root Cause Coverage

### PASS — All 5 root causes are addressed

| Solution | Root Cause | Coverage |
|----------|-----------|----------|
| 1. class→method auto-expand | RC1: class vs method query granularity | Direct. The class-kind gate in `find_callers` + `methods_of()` via Contains edges is the correct approach. |
| 2. search --class | RC2: search doesn't return class methods | Direct. Combines name search + Contains-edge traversal as a shortcut. |
| 3. C++ operator noise filter | RC4: operator noise floods callee lists | Direct. The 10:1 noise ratio justifies default filtering. |
| 4. Framework boundary annotation | RC3: framework callbacks have 0 callers | Partial. The spec adds passive annotation, which addresses the "don't confuse users" aspect. |
| 5. orchard audit | RC5: iOSLogin module partial coverage | Diagnostic only. Confirms the gap but does not fix it. |

### ISSUE — Root Cause 3 is only partially addressed

The RCA explicitly recommends two actions for RC3: (a) annotate framework boundaries, and (b) enable **reverse tracing from callees** as a fallback ("从 callees 方向反向追溯"). Solution 4 implements (a) via `open_gaps` text but does not implement (b) — it doesn't automatically suggest or execute a `find_callees` lookup when callers are empty. The annotation text *mentions* reverse tracing ("Use reverse tracing via find_callees"), but this is a suggestion to the human, not an automated fallback. Consider whether the handler should auto-return callee data (or a link to it) when `callers_of` returns empty for a known framework callback pattern.

### ISSUE — Root Cause 5 fix is explicitly out of scope

The RCA finding on iOSLogin states: "可能编译为 Swift module 但 ObjC 接口未被 IndexStore 完整记录；或 IndexStore 来自不包含 iOSLogin target 的构建配置。" This directly implies the ingest flow needs multi-target/scheme support — you may need to ingest IndexStore data from multiple build configurations to get full coverage. However, the spec's "非目标" section states: "不改变 IndexStore ingest 流程 — 方案 5 只诊断，不自动修复覆盖问题。" This creates a known design gap: the audit command will confirm the problem exists, but the system cannot fix it. See Q6 for deeper analysis.

---

## Q2: Design Trade-Offs

### PASS — Noise filter default-on with explicit escape hatch

Default-on filtering is the correct design decision. The evidence from the RCA (89 callees, ~80 noise → 10:1 ratio) means the unfiltered output is functionally useless for most queries. The `--include-noise` flag provides a clean, discoverable escape hatch. The metadata annotation (`noise_removed: N`, `total_raw: N`) is good transparency practice.

### PASS — Auto-expand is the right choice over prompt-select

For `find_callers` with a class USR, auto-expanding to all method callers is the correct default. A prompt-select flow ("which of these 15 methods do you want callers for?") would add friction to the 90% case where the user wants all of them. The `via_method` annotation preserves method-level provenance.

### SUGGESTION — Auto-expand needs a guard for large classes

The spec does not address what happens when a class has 100+ methods (common in large UIKit view controllers, God objects, or generated code). Auto-expanding callers for 100 methods could return thousands of results with no intermediate grouping. Consider:
- A configurable `--max-methods` cap (default e.g. 50) that truncates with a warning
- Or: group results by method but return a summary count first, allowing the user to drill down

### ISSUE — Noise filter substring matching is fragile

The noise filter uses `if pattern in name` (substring match), which will produce false positives:
- `"stream"` matches `uploadStream`, `dataStream`, `streamAudioData` — legitimate business methods
- `"str"` matches any method containing "str" — `destroy`, `restore`, `construct`, `registration`, `strongPassword`, etc.
- `"application:"` in the framework patterns (Q4) would match `configureApplication:withSettings:` — a legitimate business method, not a framework callback

At minimum, use word-boundary or prefix matching. Better: make the pattern list a configurable JSON file so users can tune it per-project without changing code.

### PASS — `open_gaps` field reuse is architecturally clean

Solution 4's use of the existing `open_gaps` list in `BaseToolResponse` is correct. The field already carries "no callers found" as a string, and adding a more specific diagnostic string is an additive, non-breaking change. No new response fields needed.

---

## Q3: Priority Ordering

### PASS — P0 (1+2) then P1 (3→4→5) is correct

The ordering matches the impact-to-user relationship:
- **P0 (1+2)**: These fix the primary discoverability failure — users can't find callers for class symbols, and can't discover methods of a class. They unblock the core query workflow.
- **P1 (3→4→5)**: These improve output quality (noise filter), reduce confusion (framework annotation), and enable diagnosis (audit).

The sub-ordering within P1 (3 first, then 4, then 5) is also correct: noise filtering has the broadest impact (affects every callee query), followed by framework annotation (affects only zero-result queries), followed by audit (infrequent diagnostic use).

### SUGGESTION — Upgrade RC3 reverse-tracing to P1

If the reverse-tracing aspect of RC3 (auto-fallback to callees when callers are empty) is added, it should sit alongside Solution 4 at P1, or even between Solution 3 and Solution 4. The "0 callers → try callees" path is a common user workflow that should be automated.

---

## Q4: Missing Edge Cases and Failure Modes

### ISSUE — Solution 1 dedup by caller USR loses call-site fidelity

The spec's dedup logic:
```python
for c in all_callers:
    if c["usr"] not in seen:
        seen.add(c["usr"])
        unique.append(c)
```
This deduplicates by caller USR, which means if `performSetup` calls both `acceptVideoCall:` and `createInvocation:` on the same class, only one entry appears — and the `via_method` annotation is set to whichever method was encountered first. The result under-reports the call relationship. Consider grouping callers by method without dedup, or returning a `via_methods: ["acceptVideoCall:", "createInvocation:"]` list.

### ISSUE — Solution 1 `find_callees` auto-expand is underspecified

The spec states "同样修改 `find_callees`: class 级别展开为所有 method callees 聚合" but provides zero implementation detail. Callee expansion has different characteristics: methods within a class may share many callees (e.g., both `init` and `configure` call `self.someProperty`), making the dedup question more significant. A class with 15 methods, each with 20 callees, could produce 300 callee entries — most of which are shared. The spec should address whether callee dedup happens at the callee level (like callers) or if some aggregation is needed.

### ISSUE — Solution 4 regex patterns are too broad

The pattern `r"application:"` will match `configureApplication:`, `resetApplication:`, `migrateApplicationData:` — all legitimate business methods, not UIKit delegate callbacks. The regex should anchor to known selector prefixes. For example:
- `r"^application:"` → only methods starting with `application:`
- `r"^application(Will|Did)"` → more precise

Similarly, `r"numberOfSections"` and `r"numberOfRows"` are substring matches that could hit unrelated methods. These should be anchored: `r"^numberOfSections(In|$)"` or use exact method signatures from the Apple SDK.

### SUGGESTION — Solution 2 --class with multiple matches

The spec returns `{"owner": ..., "methods": [...]}` but does not describe what happens when `search --class "Helper"` matches `ZPJoinConfHelper`, `ZPConfHelper`, and `NetworkHelper`. The response should be a list of owner+methods pairs, not a single object.

### SUGGESTION — Solution 2 --kind interaction

The CLI signature shows `--kind method` but doesn't clarify whether non-method kinds (e.g., `--kind property`, `--kind protocol`) would error or return empty. If `--kind` is only valid for `method` when used with `--class`, this constraint should be explicit.

### SUGGESTION — Solution 5 audit: xcodebuild dependency is fragile

The audit command spec mentions `xcodebuild -list` to enumerate targets. This assumes:
1. `xcodebuild` is available on PATH
2. The project builds successfully (or at least resolves its workspace)
3. There is exactly one `.xcworkspace` or `.xcodeproj`

None of these are guaranteed. The spec should define fallback behavior: what if xcodebuild fails? What if there are multiple workspaces? What if the project uses SwiftPM instead of xcodeproj?

---

## Q5: Scope Assessment

### PASS — Scope is well-constrained

The "非目标" section correctly bounds the work:
- No breaking changes — all CLI signatures are additive
- No DB schema changes — query-layer only
- No ingest changes — read-only from existing graph

The 5 solutions collectively form a coherent "query usability" batch. They don't leak into unrelated concerns (indexing, schema, MCP protocol).

### SUGGESTION — Solution 3 noise patterns are broader than "C++ operator"

The solution is titled "C++ operator 噪音过滤" but the pattern list includes Foundation patterns (`defaultCenter`, `postNotificationName:object:`) that are not C++ operators. The spec should either rename the filter to "query noise filter" (more honest) or split into C++ operator patterns + ObjC system patterns as two sub-filters.

---

## Q6: iOSLogin Investigation → Design Changes

### ISSUE — The investigation demands ingest-flow changes, which the spec excludes

The RCA's iOSLogin finding has two layers:

**Layer 1 (symptoms)**: `ZMAppLoginHelper` and related classes are missing from the graph, but `LoginServiceProtocol` exists in `iOSServiceManager` module. Symbol counts are anomalously low.

**Layer 2 (cause)**: The investigation concludes the most likely cause is that either:
- (a) the iOSLogin module is compiled as a Swift module but ObjC interfaces aren't fully recorded in IndexStore, or
- (b) the IndexStore was generated from a build configuration that doesn't include the iOSLogin target

Both causes point to the same design requirement: **the ingest flow must support multiple IndexStore paths and/or multiple build configurations**. A single `IndexStore` directory from one scheme does not capture all targets in a multi-target workspace.

### Design implications for audit command

Given this finding, `orchard audit` should:

1. **Accept multiple `--index-store` paths** (not just one db): `orchard audit --index-store path1 --index-store path2 --project-dir .`
2. **Compare against xcodebuild scheme list**, not just target list — because a target may exist in the workspace but not in any scheme that was built
3. **Report "coverage by scheme"** in addition to "coverage by target": target X has 0 symbols because it's not in any built scheme

### Design implications for ingest flow (currently excluded, but should be tracked)

Even if ingest changes are deferred, the spec should acknowledge this dependency. Suggested addition to "非目标":
> 不改变 IndexStore ingest 流程 — 方案 5 只诊断，不自动修复覆盖问题。已知后续需要支持多 IndexStore 路径 ingest (multi-scheme / multi-target) 来解决 iOSLogin 类覆盖 gap，此项作为独立的 ingest 优化跟进。

### SUGGESTION — Add a --fix hint to audit output

The audit command could report actionable recommendations:
```
iOSLogin: 345 symbols (expected ~15,000 based on file count)
→ Suggestion: iOSLogin target may not be in the build scheme used for indexing.
  Run: xcodebuild -workspace Zoom.xcworkspace -scheme AllTargets -sdk iphoneos build
  Then re-run: orchard ingest --index-store <new-path>
```

This bridges the gap between diagnosis and fix without implementing auto-fix.

---

## Summary of Findings

| # | Tag | Finding |
|---|-----|---------|
| 1 | ISSUE | Solution 1 dedup by caller USR loses call-site fidelity when a caller invokes multiple methods |
| 2 | ISSUE | Solution 1 `find_callees` auto-expand is underspecified — no implementation detail, different dedup characteristics |
| 3 | ISSUE | Solution 3 substring matching (`"str"`, `"stream"`) will produce false positives on legitimate business methods |
| 4 | ISSUE | Solution 4 regex patterns are unanchored and will match unrelated methods (e.g., `configureApplication:`) |
| 5 | ISSUE | RC3 reverse-tracing (auto-fallback to callees) is recommended by RCA but not implemented |
| 6 | ISSUE | iOSLogin root cause demands multi-IndexStore ingest, but the spec explicitly excludes ingest changes |
| 7 | SUGGESTION | Auto-expand needs a guard for classes with 100+ methods (cap or summary view) |
| 8 | SUGGESTION | Noise filter patterns should be configurable (JSON file) rather than hardcoded |
| 9 | SUGGESTION | Framework boundary patterns should cover macOS/AppKit delegates, not just iOS/UIKit |
| 10 | SUGGESTION | Solution 2 should clarify response format for multiple class matches and `--kind` constraints |
| 11 | SUGGESTION | Audit command should accept multiple `--index-store` paths and handle xcodebuild failures gracefully |
| 12 | PASS | All 5 root causes are addressed; scope is well-constrained; P0→P1 ordering is correct; escape hatches are present; `open_gaps` reuse is architecturally clean |

## Verdict

The design is sound and well-scoped. The 6 ISSUE findings should be resolved before implementation begins — the most consequential being (a) the dedup fidelity problem in Solution 1, (b) the noise filter substring false-positive risk, and (c) the explicit acknowledgment that RC5 requires ingest-flow changes beyond the audit command. The SUGGESTION items are quality improvements that can be deferred to implementation review.
