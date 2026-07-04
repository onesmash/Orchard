# Orchard — Apple 语义图

> 面向 AI 代理的编译器级 Apple 平台代码智能。

Orchard 从 Xcode IndexStore 构建语义代码图谱 — 每条边都由编译器验证，而非启发式推断。通过 CLI 和 MCP 服务器，为 Swift、Objective-C、C、C++ 代码库提供调用图分析、影响评估、类型层次和语义搜索。

## 引导式搜索

- 使用 `orchard_search` 按名称或限定名查找符号。
- 使用 `orchard_lookup_frame` 解析单个堆栈帧或帧样式的符号文本，获取所属类/方法的图谱上下文。
- 完整的 crashlog 和 crash 线程块在 Orchard 外部处理。在调用 Orchard 之前，先提取出具体的帧、符号名、限定名或 USR。
- 如果引导式搜索结果中包含 `orchard_refresh_index`，请先执行文档中说明的 Orchard 索引刷新命令，再采信缺失结果。

## 功能特性

- **编译器验证的边** — 数据来源是 Xcode IndexStore，调用关系是 ground truth，而非正则近似匹配
- **MCP 服务器** — 作为常驻子进程运行，供 Claude Code / Claude Desktop 使用，提供搜索、crash 帧查找、调用者、被调用者、引用、影响分析、符号元数据、类型层次、统计和审计工具
- **自动发现** — 检测 `.xcworkspace`/`.xcodeproj`，匹配 DerivedData，自动定位 IndexStore
- **噪音过滤** — 默认排除 C++ 运算符重载、日志宏和流辅助函数；推断边为按需开启
- **影响分析** — 按深度分组的 blast radius（d1 = 直接调用者/必定受影响，d2 = 间接/可能受影响，d3+ = 传递依赖）
- **跨语言桥接** — 追踪 ObjC ↔ Swift 调用边
- **社区检测** — Leiden 算法发现模块边界
- **执行流** — 从调用图自动检测执行流程
- **混合搜索** — BM25 + 向量嵌入实现语义代码搜索
- **一键集成** — `orchard setup` 配置 MCP 服务器、安装 Orchard 技能包、下载嵌入模型、注入代码智能块到 CLAUDE.md

## 快速开始

### 前置条件

- Python >= 3.12
- 一个有最新编译的 Xcode 项目（会产生 IndexStore 数据）

### 安装 `uv`

如果尚未安装 `uv`，请先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装后请重启 shell，或确保 `uv` 在 `PATH` 中。

### 安装

```bash
# 方式一：从 git 安装为全局 CLI 工具
uv tool install git+ssh://git@git.zoom.us/ai-tools/orchard.git

# 方式二：clone + 本地开发和安装
git clone git@git.zoom.us:ai-tools/orchard.git
cd orchard
uv tool install -e .
```

### 索引你的项目

```bash
# 从项目目录自动检测一切
orchard ingest --project-dir /path/to/YourXcodeProject

# 背后发生了什么：
# 1. 找到 .xcworkspace / .xcodeproj → 推导 scheme/target
# 2. 通过 Info.plist WorkspacePath 匹配 DerivedData
# 3. 定位 IndexStore
# 4. 摄入符号、调用、包含、继承和实现关系
# 5. 将图谱写入 <project>/.orchard/graph.db
```

### 一键 Claude Code 集成

```bash
orchard setup
```

此行命令配置 Claude Code 所需的一切：

- ✅ `settings.json` 中的 MCP 服务器条目
- ✅ `.claude/skills/` 中的 Orchard 技能包
  （`orchard`、`orchard-cli`、`orchard-debugging`、`orchard-exploring`、
  `orchard-impact-analysis`）
- ✅ 嵌入模型下载
- ✅ 注入到 `CLAUDE.md` / `AGENTS.md` 的代码智能块

使用 `--mcp`、`--skill`、`--model` 或 `--claude-md` 可单独安装各组件。
`orchard setup --skill` 安装完整的 Orchard 技能包。

### 更新 Orchard

```bash
orchard update
```

此命令通过 `uv tool upgrade orchard` 升级已安装的 Orchard CLI。
添加 `--setup` 可同时刷新本地 MCP / skill 集成：

```bash
orchard update --setup
```

## 使用方式

### CLI

```bash
# 通过名称搜索符号（子串匹配）
orchard search --name "viewDidLoad" --kind method --language swift

# 谁调用了这个符号？
orchard find_callers --usr "s:MyClass::myMethod()"

# 这个符号调用了谁？
orchard find_callees --usr "s:MyClass::myMethod()" --include-inferred

# Blast-radius 影响分析
orchard impact --usr "s:MyClass::myMethod()"

# 类型层次（父类、协议、子类）
orchard hierarchy --usr "c:MyModule::MyClass"

# 符号元数据
orchard symbol --usr "s:MyClass::myMethod()"

# 数据库统计
orchard stats

# 模块覆盖率审计
orchard audit --project-dir /path/to/project
```

### MCP 服务器

MCP 服务器设计为由 Claude Code 作为子进程启动：

```bash
orchard-mcp [--db /path/to/graph.db]
```

提供以下核心工具：

| 工具 | 说明 |
|------|------|
| `orchard_search` | 按名称搜索符号或列出类的方法 |
| `orchard_lookup_frame` | 将单个堆栈帧解析为所属类/方法的图谱上下文 |
| `orchard_find_callers` | 查找符号的所有调用者（支持多跳） |
| `orchard_find_callees` | 查找符号调用的所有符号（支持多跳） |
| `orchard_find_references` | 传入 + 传出引用 |
| `orchard_impact` | 带深度分组的 blast-radius 分析 |
| `orchard_symbol` | 符号元数据（名称、类型、语言、模块） |
| `orchard_hierarchy` | 类型层次（父类、协议、遵循者） |
| `orchard_stats` | 数据库统计 |
| `orchard_audit` | 模块覆盖率报告，含 Xcode target 缺口检测 |

### 信号过滤

| 参数 | 默认值 | 效果 |
|------|--------|------|
| `include_noise` | `false` | 显示 C++ 运算符和日志辅助函数 |
| `include_inferred` | `false` | 显示编译器推断的边 |

默认情况下，仅返回源代码级的调用证据 — 即编译器验证过的调用点，而非启发式推断。

### 数据库发现

命令会自动查找数据库：

1. `--db <path>` 参数
2. `ORCHARD_DB_PATH` 环境变量
3. 从当前目录向上查找第一个 `.orchard/graph.db`（项目级）
4. `~/.orchard/graph.db`（全局回退）

通常不需要显式传递 `--db` — 只需在项目目录下的任意位置运行查询即可。

## 架构

```
Xcode IndexStore                    ┌─────────────────────────┐
（编译器产物）                        │       MCP Server         │
         │                          │  (orchard-mcp, stdio)    │
         ▼                          │  search / callers /      │
┌─────────────────┐                 │  callees / impact /      │
│  ingest/         │                │  hierarchy / stats /     │
│  indexstore.py   │──▶ graph.db ──▶│  audit / references /    │
│  symbolgraph.py  │   (Ladybug/    │  symbol                  │
└─────────────────┘    DuckDB)      └─────────────────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
   ┌──────────┐          ┌────────────┐         ┌────────────┐
   │ derive/  │          │  search/    │         │  query/    │
   │ arch     │          │  hybrid     │         │  lookup    │
   │ bridge   │          │  embedder   │         │  noise     │
   │ community│          │  chunker    │         │  filter    │
   │ process  │          └────────────┘         └────────────┘
   │ mro      │
   └──────────┘
```

### 数据管线

摄入过程运行一个分阶段管线：

1. **IndexStore** — 解析 `recordName.Unit` 文件 → 提取符号声明、引用和关系
2. **Symbol Graph** — 解析 `.swiftsymbolgraph` 文件获取 Swift 接口数据
3. **Build** — 创建 `BuildSnapshot` 和 `Target` 节点，关联源码根路径
4. **Normalize** — USR 标识归一化
5. **Graph** — 将符号和边插入 Ladybug/DuckDB 图谱
6. **Derive** — 摄入后处理：社区检测（Leiden）、流程检测、桥接边、MRO、架构分析

### 核心模块

| 模块 | 用途 |
|------|------|
| `orchard.cli` | CLI 入口，包含所有查询命令 |
| `orchard.server` | MCP 服务器（stdio 传输） |
| `orchard.setup` | 一键 Claude Code 配置 |
| `orchard.ingest` | IndexStore 和 Symbol Graph 解析 |
| `orchard.graph` | Ladybug/DuckDB 模式与连接 |
| `orchard.handlers` | 每个 MCP 工具的查询逻辑 |
| `orchard.derive` | 社区检测、流程检测、桥接 |
| `orchard.search` | 嵌入模型与混合搜索 |
| `orchard.query` | 图查询辅助函数与噪音过滤器 |
| `orchard.pipeline` | 基于阶段的摄入管线执行器 |
| `orchard.build` | Xcode 项目/编译发现 |
| `orchard.validation` | 索引新鲜度校验 |

## 环境要求

- Python >= 3.12
- [igraph](https://igraph.org/) — 图算法库（Leiden 社区检测）
- `ladybug` — 基于 DuckDB 的图数据库（内部）
- `leidenalg` — Leiden 社区检测
- `llama-cpp-python` — 本地嵌入模型
- `mcp` — MCP Python SDK

## 开发

```bash
# 安装开发依赖
uv sync

# 运行测试
uv run pytest

# 构建 wheel
uv build
```

## 许可证

专有软件 — Zoom Video Communications, Inc.

## 参考

- [MCP (Model Context Protocol)](https://modelcontextprotocol.io/)
- [igraph — 图算法库](https://igraph.org/)
- [Leiden 社区检测算法](https://github.com/vtraag/leidenalg)
- [llama.cpp — 本地 LLM 推理](https://github.com/ggml-org/llama.cpp)
