# Testing Perspective Review -- Orchard Optimization Batch 3

**Date**: 2026-06-26
**Reviewer**: Testing review agent
**Spec**: `docs/superpowers/specs/2026-06-26-orchard-optimization-batch-3.md`
**Verdict**: Ready for implementation planning, but only if the plan treats this batch as integration-test heavy work rather than pure unit work.

## Summary

这轮优化的风险不在“算法写错”，而在“已有实现存在，但接线层没真正把它们用对”。因此测试重点必须放在：

1. ingest 入口是否真的执行了新的 merge precedence
2. CLI 默认 build/DB 选择是否真的改变了输出
3. `find_callers` 是否真的返回了新增位置字段
4. `stats` 是否真的展示了 BuildSnapshot 元信息

## Key Findings

### 1. 需要把字段级合并测试成端到端 ingest 行为

仅测一个 merge helper 不够，因为真正 bug 出在：
- `runner.py` 的 fallback 逻辑
- `identity.py` 的 insert-only 语义

**建议测试**：
- 给同一 USR 构造一份 SymbolGraph 记录和一份 IndexStore 记录
- 断言 ingest 后 `file_path` 取 IndexStore，`name` 取 IndexStore，`swift_display_name` 取 SymbolGraph

### 2. `find_callers` 必须做 handler 级回归

这不是 query helper 的单元测试能覆盖的，因为输出 shape 会变化。

**建议测试**：
- callers 响应新增 `file_path`, `line`, `col`
- 旧字段 `usr`, `name`, `module`, `kind`, `language`, `owner`, `depth` 保持兼容

### 3. freshness 修复必须覆盖“未显式传 build_id”的默认路径

handoff 里这个问题的根因就在 CLI 装配层，因此只测 `freshness_for()` 不足以证明修复。

**建议测试**：
- CLI/pipe/handler 请求不传 `build_id`
- 图中存在最新 BuildSnapshot
- 断言返回 `freshness != "stale"`

### 4. CLI 提示行为要做输出层断言

这轮有几项修复本质上是“输出诊断增强”，例如：
- 使用父目录 DB 时打印提示
- `stats` 打印 DB 路径和 snapshot 元信息
- `source-root` 过滤到 0 时打印 warning

这些都需要测试 stdout/stderr，而不只是函数返回值。

### 5. Phase 2 的旧 USR 清理若本轮落地，必须有保守回归

这是本批最危险的连续推进项。

**最低要求**：
- 当前 build 消失的旧 USR 会被清掉
- 同 target 当前 build 仍存在的 USR 不误删
- 不同 target 的同名 / 不同 build 数据不被误删

## Implementation Readiness

结论是 **ready for planning**。但实现计划里必须显式列出：

1. 需要新增的 integration tests
2. 需要覆盖 stdout/stderr 的 CLI tests
3. Phase 2 若推进旧 USR 清理时的误删防线
