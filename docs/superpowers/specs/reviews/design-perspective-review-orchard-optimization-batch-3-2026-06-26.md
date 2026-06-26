# Design Perspective Review -- Orchard Optimization Batch 3

**Date**: 2026-06-26
**Reviewer**: Design / architecture review agent
**Spec**: `docs/superpowers/specs/2026-06-26-orchard-optimization-batch-3.md`
**Verdict**: Design direction is sound and appropriately user-centered. Ready for implementation planning with one architectural guardrail: keep common-path semantics simple and additive.

## Summary

这份 spec 最好的地方，是它没有把 Orchard 的当前问题定义成“搜索能力不够强”，而是先抓住了更基础的复杂度来源：

1. 同一符号来自两路数据源时，系统行为不透明。
2. CLI 默认值让用户看不出 Orchard 连接的是哪个 DB、哪个 build。
3. 查询输出不够自描述，导致人必须手动补上下文。

这符合“先拉低认知负担，再做能力深化”的设计原则。

## Key Findings

### 1. 这轮应该优先做“深模块”而不是“分散修补”

最值得坚持的设计点是：把跨源合并做成一个集中模块，而不是把“路径覆盖”“名称覆盖”“freshness 修复”散落到多个 handler 里各自补丁。

**原因**：
- 同一设计决策若散落在 `runner.py`、`identity.py`、`lookup.py` 多处，会形成信息泄漏
- 后续维护者很难知道“真实的主定义字段优先级”到底在哪定义

**建议**：
- merge precedence 只在一个地方定义一次
- 其他查询和 ingest 逻辑只消费 merge 后的稳定字段

### 2. additive schema 优于复用现有字段做隐式双语语义

如果 `name` 同时承担“主显示名”“ObjC selector”“Swift 展示名”三种角色，接口会越来越浅，调用方反而需要记住更多约定。

**建议**：
- `name` 保持单一职责：主显示名
- `swift_display_name` 明确表达第二视图
- future bridge work 再消费这两个稳定字段

这比把双语含义折叠进一个字段更深，也更容易理解。

### 3. CLI 默认行为应持续朝“显式上下文”演进

spec 里对 DB 路径发现、stats 元信息、freshness 默认 build 的处理是正确方向。它们共同做的是一件事：把 Orchard 从“静默猜测系统”变成“可解释系统”。

这属于高价值设计，不只是小 UX 修补。

### 4. Phase 2 不应反过来污染 Phase 1 接口

旧 USR 清理、inline 语义标注、USR-based bridge recovery 都值得做，但不要为了提前给 Phase 2 铺路，把 Phase 1 接口设计得很重。

**建议**：
- Phase 1 只引入当前已经确定会被直接消费的字段和 helper
- 对 Phase 2 保留扩展点，但不要让常用路径先背上不必要复杂度

## Implementation Readiness

结论是 **ready for planning**。计划阶段建议强制写出两份简短契约：

1. `merged symbol field precedence`
2. `CLI default build / DB selection rules`

只要这两份契约先写清楚，后面的代码会明显更稳。
