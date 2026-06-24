# Testing Perspective Review — Orchard Python Implementation

**Date**: 2026-06-24  
**Reviewer**: Testing subagent

## 总分：3/5（可测性中等偏低）

## 各维度评分

| 维度 | 分数 | 关键发现 |
|---|---|---|
| 验收用例覆盖度 | 3/5 | 缺 partially_stale、toolchain_mismatch、confidence gate、SPM 场景 |
| 可测性设计 | 4/5 | PhaseResult stats/warnings 基础好；缺 duration_ms、warnings 结构化 |
| 测试隔离性 | 3/5 | subprocess 调用和 IndexStore 摄取无隔离策略；Ladybug in-memory 未提及 |
| 边界条件覆盖 | 2/5 | confidence < 0.70 gate 无验收用例；partially_stale/toolchain_mismatch 无覆盖 |
| 测试工程结构 | 3/5 | 缺 test_validation/；fixture 策略空白；无性能基准 |

## 需补充的验收场景

1. **partially_stale**：多 target 工程仅部分 target 重建，查询返回 `freshness = partially_stale`
2. **toolchain_mismatch**：Xcode 版本变更后查询，返回 `freshness = toolchain_mismatch` + 风险上调
3. **confidence < 0.70 gate**：低置信 bridge 不出现在 `impact_analysis` 默认结果中
4. **空构建产物**：IndexStore 为空时 PhaseResult.warnings 包含诊断信息
5. **SPM 工程**：`swift_build` build_system 的验收

## 需补充的测试工程设计

- **Fixture 策略**：明确使用预录制 JSON 产物（推荐，可跨平台）还是真实 Xcode 工程构建
- **Ladybug in-memory**：确认 `ladybug.Database(":memory:")` 是否支持，用于单元测试
- **subprocess mock**：明确 subprocess 调用（xcodebuild、SourceKitten）的测试替身策略
- **test_validation/**：增加对 `validation/audit.py` 和 `validation/freshness.py` 的测试目录

## 结论

测试基础框架规划合理，但关键边界条件（freshness 枚举、confidence gate）的验收覆盖缺失较多，建议同步补充进 spec，并在 Milestone 0 时确定 fixture 策略。
