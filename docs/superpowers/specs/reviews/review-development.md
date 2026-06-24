# Development Perspective Review — Orchard Python Implementation

**Date**: 2026-06-24  
**Reviewer**: Development subagent

## 总分：3/5（有条件就绪）

## 各维度评分

| 维度 | 分数 | 关键发现 |
|---|---|---|
| 技术可行性 | 3/5 | Ladybug OK（已核实）；swift-index-store 无 PyPI 包是阻塞项 |
| 依赖风险 | 3/5 | Ladybug `pip install ladybug` v0.17.1 已确认为 LadybugDB 图数据库，无冲突；swift-index-store 无现成 Python 绑定 |
| Phase DAG 实现可行性 | 3/5 | bridge recovery 依赖 .swiftinterface 存在性；DAG 调度器未选型 |
| 仓库结构合理性 | 4/5 | 分层清晰；normalize/bridge.py 与 derive/bridge.py 需要命名区分 |
| MCP 工具接口 | 4/5 | mcp Python SDK 兼容；streaming 支持需确认 |

## 关键风险

| 优先级 | 风险 | 处置 |
|---|---|---|
| 🔴 阻塞 | swift-index-store 无 PyPI Python 绑定 | 方案 A：提供薄 Swift CLI wrapper（orchard-indexstore-reader）随包发布，subprocess 调用；方案 B：直接用 ctypes 访问 IndexStoreDB C API |
| 🟡 中 | ObjC↔Swift bridge recovery 依赖 .swiftinterface | 增加 availability check，降级到 subprocess clang extractor |
| 🟡 中 | Phase DAG 调度器未选型 | asyncio + 手写 DAG 足够，明确即可 |

## 结论

Ladybug 包名风险已排除（`pip install ladybug` v0.17.x = LadybugDB 图数据库）。
IndexStore 摄取层需要在 Milestone 0 做技术 spike，确认 Swift CLI wrapper 方案可行性。
