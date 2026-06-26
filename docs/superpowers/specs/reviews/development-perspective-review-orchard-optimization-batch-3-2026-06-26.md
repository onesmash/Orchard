# Development Perspective Review -- Orchard Optimization Batch 3

**Date**: 2026-06-26
**Reviewer**: Development review agent
**Spec**: `docs/superpowers/specs/2026-06-26-orchard-optimization-batch-3.md`
**Verdict**: Ready for implementation planning after tightening two contracts: symbol merge precedence and phase-2 deletion semantics.

## Summary

这份 spec 的主线方向是对的，尤其是把“查询结果正确性”和“CLI 可解释性”放在最前面，能直接降低 Orchard 当前最痛的认知负担。实现上最重要的是避免把多个问题揉进一次大重构里，因此建议继续坚持：

1. Phase 1 先做字段级合并、freshness 默认 build、stats/DB 提示与 callers 定位补全。
2. Phase 2 再推进旧 USR 清理和 bridge 恢复算法深化。

## Key Findings

### 1. 双来源合并应在进入 `upsert_symbols()` 之前完成

当前代码里：
- `pipeline/runner.py` 只有“没有 SymbolGraph 才 fallback 到 IndexStore symbol descriptors”
- `normalize/identity.py::upsert_symbols()` 只插入，不更新

因此如果不先在内存里完成按 USR 的 merge，而是继续尝试“先写 SymbolGraph 再补写 IndexStore”，会马上撞上已有的 insert-only 语义，路径错误问题不会真修掉。

**建议**：
- 新增明确的 merge helper
- 把 SymbolGraph / IndexStore 两侧记录规整成一个统一 `SymbolRecord`
- 再只调用一次 `upsert_symbols()`

### 2. `swift_display_name` 需要明确为 additive schema

如果本轮要同时保留 ObjC 原生名和 Swift 展示名，最稳的方式是加字段而不是复用 `name` 做混合语义。

**建议**：
- `name` = 主显示名，优先 IndexStore 原生名
- `swift_display_name` = SymbolGraph 提供的 Swift 名
- 不要让调用方再猜 “当前 `name` 到底是哪一种语言视图”

### 3. `find_callers` 的 line/col 要接受可降级事实，但查询接口要先留位

当前图里 callers 查询只从 `Symbol` 节点取基础字段，没有 Occurrence 层的稳定定义位置回填逻辑。直接承诺“本轮一定精确到 line/col”有风险。

**建议**：
- 响应结构先标准化为 `file_path`, `line`, `col`
- 若当前图无法稳定回填，允许 `line/col` 暂空，但查询层和测试先把字段契约固定下来
- 后续若补 Occurrence / definition lookup，不再改响应 shape

### 4. Phase 2 的旧 USR 清理要避免“删历史 build”

spec 现在已经把 #2 放到连续推进，但开发上要先卡一条边界：

**不要** 在没有 snapshot 语义保护时直接按“当前 build 不存在就删 DB 里的全部旧 USR”执行。

否则多 target / 多次 ingest / 共享 DB 的场景里，很容易误删本来还需要保留的节点。

**建议**：
- 若本轮推进 #2，优先做 target + build-snapshot 作用域内的 prune
- 或至少先做可控的 CLI ingest 路径，再扩展到 pipeline

## Implementation Readiness

结论是 **ready for planning**。实现计划里需要把下面两点写清楚：

1. merge helper 放在哪一层，是 `runner.py` 里组合，还是 `normalize/identity.py` 新增纯函数。
2. `swift_display_name`、`stats` 元信息、默认 latest build helper 的 schema / query 影响范围。
