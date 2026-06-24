# Design Perspective Review — Orchard Python Implementation

**Date**: 2026-06-24  
**Reviewer**: Architecture design subagent

## 总分：4.0/5

## 各维度评分

| 维度 | 分数 | 关键发现 |
|---|---|---|
| 架构分层合理性 | 4/5 | build→ingest→normalize→derive→graph→search→mcp 整体清晰；normalize/bridge.py 与 derive/bridge.py 命名混淆 |
| 图 Schema 设计 | 4/5 | USR 单主键在多 target 场景有冲突风险；缺 ConformsTo 关系 |
| 接口设计质量 | 4/5 | BaseToolRequest/Response 基类扎实；max_depth 放基类有歧义；派生层工具 confidence 应强制 |
| 派生层与事实层分离 | 5/5 | 文档最清晰的维度，Origin 枚举、provenance 字段、SwiftUI derived 标记均到位 |
| 可扩展性 | 3/5 | Apple 平台深度耦合，无 BaseIngester 抽象接口；Language Literal 硬编码 |

## 需修复的设计问题

### 高优先级

**Symbol 主键多 target 冲突**：当前 `Symbol.usr STRING PRIMARY KEY` 在同一工程不同 target 编译同一符号时（如 Debug/Release + 条件编译）可能产生主键冲突。

建议方案：
- 方案 A：改为 `(usr, target_id)` 复合主键
- 方案 B：USR 加 target 前缀（`target_id:usr`）保持单主键

### 中优先级

- `normalize/bridge.py` 改名为 `normalize/crosslang.py` 避免与 `derive/bridge.py` 混淆
- 定义 `BaseIngester` protocol，Apple 实现作为具体子类
- 将 `max_depth` 从 `BaseToolRequest` 移至具体工具 Request 子类

### 低优先级

- 增加 `ConformsTo(FROM Symbol TO Symbol)` Relation Table
- 派生层工具（SwiftUI、architecture、bridge heuristic）的 Response 中 `confidence` 改为必填

## 结论

设计质量高于平均水平，派生层分离和 freshness 元数据设计特别扎实。主要修复项是 Symbol 主键的多 target 唯一性问题（这是实现阶段的硬 bug 风险），建议在 spec 更新中明确主键策略后再进入规划。
