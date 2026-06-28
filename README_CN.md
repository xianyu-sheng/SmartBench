# SmartBench v0.6

**通用 AI 代码诊断平台** — 任意语言、任意框架、任意项目，即开即用。**零幻觉保障。**

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 📺 演示视频

<div align="center">

[![🎬 观看演示视频](https://img.shields.io/badge/🎬-观看演示视频-ff6600?style=for-the-badge&logo=vimeo&logoColor=white&labelColor=333333)](https://xianyu-sheng.github.io/SmartBench/demo.mp4)

**👆 点击上方按钮观看 SmartBench 完整操作演示**

</div>

<video src="https://xianyu-sheng.github.io/SmartBench/demo.mp4" controls width="100%">
  您的浏览器不支持视频播放，请<a href="https://xianyu-sheng.github.io/SmartBench/demo.mp4">点击下载观看</a>
</video>

> 💡 视频托管于 GitHub Pages，也可直接访问：https://xianyu-sheng.github.io/SmartBench/demo.mp4

---

## 项目简介

SmartBench 是一款**通用代码智能诊断平台**。它利用 LLM 驱动的多 Agent 辩论引擎，
对任意代码仓库进行深度分析。无论是 Python CLI 工具、Go 微服务、Rust 库还是 Java 单体应用 —
只需指向项目路径，SmartBench 自动识别语言和框架，构建代码图，产出可落地的诊断报告。

**核心原则**：SmartBench 只分析、只建议，**绝不修改**被测项目的任何代码。

### 五阶段诊断流水线（v0.6 增强版）

```
$ smartbench
  │
  ├─ 阶段 1 ─ 项目指纹采集（零 LLM 调用）
  │   确定性扫描文件系统，检测语言、框架、项目类型、构建系统、依赖关系。
  │
  ├─ 阶段 2 ─ LLM 项目理解（可选）
  │   LLM 阅读 README.md + 指纹信息，理解项目用途和领域。
  │
  ├─ 阶段 3 ─ 诊断策略选择
  │   LLM 从已验证的策略模板库中选择最优策略。
  │
  ├─ 阶段 4 ─ 代码图构建 + RAG 向量索引
  │   构建调用图 + 依赖图，同时建立代码语义向量索引。
  │   支持三级嵌入后端：sentence-transformers → sklearn TF-IDF → 字符哈希。
  │
  └─ 阶段 5 ─ 多 Agent 辩论 + 证据核查
      Proposer → [Verifier 事实核查] → Critique → [Verifier 交叉核查] → Judge
      每条提案必须附证据声明，自动验证文件:行号是否存在，拦截 LLM 幻觉。
```

---

## 快速开始

### 安装

```bash
# 基础安装（核心功能）
pip install -e .

# 推荐：包含 RAG 向量检索增强（语义搜索精度更高）
pip install -e ".[rag]"

# 或者: pip install smartbench[rag]
```

需要 Python 3.10+。

> RAG 依赖（`chromadb`、`sentence-transformers`）为**可选**。不安装时自动降级为
> 内置的 numpy+json 向量存储和 sklearn TF-IDF 嵌入，功能完整，只是语义精度稍低。

### 首次运行

```bash
smartbench
```

跟随 4 步交互式向导：

```
Step 1/4 — 代码在哪里？
  Project path/URL: /path/to/your/project

Step 2/4 — 配置大模型
  Model name: deepseek-chat
  API key for deepseek-chat: **********
  saved: sk-****b3e4
  OK deepseek-chat (DeepSeek)

Step 3/4 — 正在分析你的项目...
  [python] [fastapi] [web_service] 142 src files (git)

Step 4/4 — 你想诊断什么问题？
  Concern: 性能问题

  正在构建代码图... 847 nodes, 4521 edges
  多 Agent 辩论中... 3 rounds, ~1850 tokens

  诊断报告:
   - 发现 #1: 优化数据库连接池 (Priority 5)
   - 发现 #2: 对热点接口添加 Redis 缓存 (Priority 4)
```

### 快速模式

配置环境变量后跳过向导：

```bash
export DEEPSEEK_API_KEY="sk-your-key"
smartbench --quick -p /path/to/project -c "performance"
```

### 命令一览

| 命令 | 说明 |
|------|------|
| `smartbench` | 完整交互式向导 |
| `smartbench quick` | 快速模式，最小交互 |
| `smartbench diagnose -p <路径>` | 仅诊断（跳过压测） |
| `smartbench check` | 查看可用诊断工具 |

---

## 支持的语言与框架

### 语言检测（14 种）

| 语言 | 检测方式 | 代码图 | 语言诊断指导 |
|------|----------|--------|-------------|
| Python | `.py`、`requirements.txt`、`pyproject.toml` | ✅ 函数/类/导入/调用 | GIL、asyncio、tracemalloc、py-spy |
| Go | `.go`、`go.mod` | ✅ 函数/结构体/接口/调用 | pprof、竞态检测、goroutine 泄漏 |
| Rust | `.rs`、`Cargo.toml` | ✅ 函数/结构体/实现/trait/调用 | clone() 开销、async、锁竞争 |
| C/C++ | `.cpp/.cc/.c/.h`、`CMakeLists.txt` | ✅ 函数/类/结构体/调用 | GDB、Valgrind、ASAN、perf、火焰图 |
| Java | `.java`、`pom.xml`、`build.gradle` | ✅ 方法/类/接口/调用 | JFR、jstack、GC 分析、Arthas |
| Kotlin | `.kt/.kts`、`build.gradle.kts` | ✅ 函数/类/调用 | JVM 工具 + 协程检查 |
| JavaScript | `.js/.mjs/.cjs`、`package.json` | ✅ 函数/类/导入/调用 | clinic.js、事件循环、Promise 异常 |
| TypeScript | `.ts/.tsx`、`tsconfig.json` | ✅ 函数/类/导入/调用 | JS 工具 + 类型推断、装饰器 |
| Ruby | `.rb`、`Gemfile` | ✅ 方法/类/调用 | ruby-prof、N+1 查询、内存膨胀 |
| Swift | `.swift`、`Package.swift` | ✅ 函数/类/结构体/调用 | Instruments、循环引用、主线程阻塞 |
| C# | `.cs` | ✅ 方法/类/接口/调用 | dotnet-trace、async 死锁、LINQ |
| Zig | `.zig`、`build.zig` | ✅ 函数/结构体/调用 | valgrind、未定义行为、分配器错配 |

### 框架检测（20+ 种）

自动识别：FastAPI、Flask、Django、Gin、Echo、Fiber、go-kit、go-zero、Kratos、
Express、NestJS、Next.js、React、Vue、Spring Boot、Axum、Actix、Rocket、gRPC、brpc 等。

---

## 核心特性

### 1. 多 Agent 辩论引擎 + 证据核查

三角色交叉验证，**Proposer → Verifier → Critique → Verifier → Judge**，层层拦截幻觉：

```
Proposer（方案提出者）  →  分析上下文，生成带 evidence_claims 的方案
    ↓
Verifier（事实核查）    →  [零 LLM] 检查每个文件:行号是否真实存在
    ↓                       自动标记：✓ 已验证 / ⚠ 部分匹配 / ✗ 不存在
Critique（交叉审查者）  →  接收核查结果，审查真实性和可行性
    ↓
Verifier（交叉核查）    →  [零 LLM] 交叉验证批判意见的引用是否真实
    ↓
Judge（最终仲裁者）     →  综合证据链，拒绝引用不存在文件的提案
```

- 每条提案**必须附带 evidence_claims**（文件路径 + 行号 + 调用链）
- 验证器是**纯磁盘 I/O**：读文件、查代码图、核对调用链，不调用 LLM
- **模糊路径解析**：LLM 写出 `src/main.js` 时自动搜索项目找到 `frontend/src/main.tsx`

### 2. 代码图 + RAG 双引擎检索

- **图检索**：调用链、文件包含关系、依赖图 — 结构精准
- **向量检索**：语义相似代码搜索 — 补充图检索覆盖不到的代码
- **混合融合**：去重排序，取长补短
- **三级嵌入后端**（自动降级）：
  | 优先级 | 后端 | 适用场景 |
  |--------|------|----------|
  | 1 | sentence-transformers (multilingual-e5) | PyTorch 可用时 |
  | 2 | sklearn TF-IDF (字符级 n-gram) | PyTorch 不可用时 |
  | 3 | 字符哈希（固定维度） | sklearn 也不可用时 |
- **三级存储后端**（自动选择）：
  | 优先级 | 后端 | 适用场景 |
  |--------|------|----------|
  | 1 | SimpleVectorStore (numpy+json) | 默认，零 C 依赖，永不崩溃 |
  | 2 | ChromaDB | 设置 `SMARTBENCH_CHROMADB=1` 时启用 |

### 3. 可插拔诊断工具

| 类别 | 工具 |
|------|------|
| 系统级 | dmesg、ps、vmstat |
| Go | pprof、竞态检测器、goroutine 分析 |
| Python | tracemalloc、py-spy、cProfile、pip check |
| C/C++ | GDB、Valgrind、ASAN、perf、火焰图 |
| Java | JFR、jstack、jmap、Arthas |
| 静态分析 | ruff/mypy/bandit (Python) / go vet/staticcheck (Go) / clippy (Rust) |

### 4. 策略选择器

LLM 从**已验证的策略模板库**中选择（而非凭空发明）：

- `performance_analysis` — CPU、内存、I/O 性能分析
- `correctness_audit` — Bug 检测、边界情况、错误处理
- `architecture_review` — 设计模式、耦合度、内聚性
- `security_scan` — 漏洞检测、依赖审计
- `hotspot_analysis` — 聚焦最近变更的热点文件（Git 感知）

### 5. 配置生命周期

- API 密钥**仅存储于内存** — 永不写入磁盘
- 每次重启终端 → 重新配置（设计如此）
- 支持 8 个 LLM 提供商，模型名称自动识别
- 密码输入 `*` 回显 + `****abcd` 格式确认

---

## 项目架构

```
SmartBench/
├── smartbench/
│   ├── cli.py                      # CLI 入口（typer + rich，三种模式）
│   ├── detector/                   # 阶段 1：零 LLM 项目指纹采集
│   │   ├── fingerprint.py          #   语言/框架/项目类型枚举 + 数据模型
│   │   └── scanner.py              #   确定性文件系统扫描器
│   ├── prompts/                    # 阶段 2-3-5：动态 Prompt 生成
│   │   ├── factory.py              #   PromptFactory — 语言感知 Prompt 构建器
│   │   └── templates.py            #   静态模板字符串
│   ├── graph/                      # 阶段 4：代码图引擎
│   │   ├── schema.py               #   CodeGraph/CodeNode/CodeEdge 数据模型
│   │   ├── builder.py              #   12 语言 AST 解析器 → 图构建
│   │   └── retriever.py            #   图增强上下文检索器
│   ├── rag/                        # 🆕 阶段 4+：RAG 向量检索
│   │   ├── chunker.py              #   语言感知代码切块器
│   │   ├── embedder.py             #   三级嵌入后端（ST→TF-IDF→哈希）
│   │   ├── store.py                #   双后端向量存储（SimpleVS→ChromaDB）
│   │   ├── indexer.py              #   索引流水线编排器
│   │   └── retriever.py            #   混合检索器（图+向量融合）
│   ├── verifier/                   # 🆕 阶段 5：证据核查引擎
│   │   ├── location.py             #   文件:行号 存在性验证 + 模糊路径解析
│   │   ├── extractor.py            #   源码提取器 + 调用链验证
│   │   ├── cross_checker.py        #   提案 vs 代码图 vs 磁盘 交叉验证
│   │   ├── scorer.py               #   评分引擎 + 幻觉标记
│   │   └── verifier.py             #   顶层编排器
│   ├── diagnostics/                # 可插拔诊断工具
│   │   ├── registry.py             #   DiagnosticRegistry + DiagnosticTool ABC
│   │   └── tools.py                #   8 个具体工具实现
│   ├── engine/                     # 分析引擎
│   │   ├── debate.py               #   多 Agent 辩论引擎（v0.6 增强：含验证轮次）
│   │   ├── diagnostic.py           #   遗留诊断引擎（保留兼容）
│   │   ├── aggregator.py           #   建议去重与排序
│   │   ├── weight.py               #   模型权重计算
│   │   ├── cache.py                #   文件 + 分析缓存
│   │   └── regression.py           #   性能回归追踪
│   ├── agents/                     # Agent 编排（v0.3 保留）
│   └── plugins/                    # 插件系统
│       ├── models/                 #   LLM 提供商插件
│       └── systems/                #   目标系统插件
├── config/
│   └── default.yaml                # 默认配置文件
├── tests/
│   ├── test_*.py                   # 单元测试
│   └── e2e_simulation.py           # 全流水线模拟测试
├── pyproject.toml                  # 构建配置 + 入口点 + 可选依赖
└── README.md
```

---

## 配置说明

### 模型与 API Key

SmartBench 根据模型名称自动识别提供商和 Base URL：

| 你输入 | 自动识别 |
|--------|----------|
| `deepseek-chat` | DeepSeek → `https://api.deepseek.com/v1` |
| `gpt-4o` | OpenAI → `https://api.openai.com/v1` |
| `claude-sonnet-4` | Anthropic → `https://api.anthropic.com/v1` |
| `glm-4` | 智谱 GLM → `https://open.bigmodel.cn/api/paas/v4` |
| `doubao-seed-2.0-pro` | 字节豆包 → `https://ark.cn-beijing.volces.com/api/v3` |
| `moonshot-v1` | 月之暗面 Kimi → `https://api.moonshot.cn/v1` |
| `qwen-max` | 阿里通义千问 → `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `llama3.1` | 本地 Ollama → `http://localhost:11434/v1` |

### 环境变量（快速模式）

```bash
export DEEPSEEK_API_KEY="sk-your-key"
export OPENAI_API_KEY="sk-your-key"
export ANTHROPIC_API_KEY="sk-ant-your-key"
export GLM_API_KEY="your-key"
export DOUBAO_API_KEY="your-key"
export MOONSHOT_API_KEY="your-key"
export DASHSCOPE_API_KEY="your-key"
```

### 配置文件（`config/default.yaml`）

```yaml
systems:
  raft_kv:                          # 遗留：特定目标系统配置
    project_path: "/path/to/project"
    benchmark_command: "./bench.sh"

models:                             # 遗留：静态模型配置
  - name: "deepseek"
    provider: "openai_compatible"
    enabled: true
```

> **说明**：`config/default.yaml` 保留用于向后兼容。
> v0.5 中，交互式 CLI 向导是主要的配置方式。

---

## 扩展指南

### 添加新语言

1. **检测** — 在 `detector/scanner.py` 的 `_EXTENSION_MAP` 中添加扩展名，在 `_MANIFEST_MAP` 中添加清单文件
2. **代码图** — 在 `graph/builder.py` 的 `_PATTERNS` 中添加正则表达式
3. **诊断指导** — 在 `prompts/factory.py` 的 `_language_specific_guidance()` 中添加语言提示
4. **诊断工具** — 在 `diagnostics/tools.py` 中创建 `DiagnosticTool` 子类

以 Ruby 为例：

```python
# 1. scanner.py — 已内置：.rb 扩展名 + Gemfile 清单文件
# 2. builder.py _PATTERNS:
Language.RUBY: {
    "function": re.compile(r'def\s+(?P<name>\w+)', re.MULTILINE),
    "class": re.compile(r'class\s+(?P<name>\w+)', re.MULTILINE),
    "call": re.compile(r'(?P<name>\w+)\s*\(', re.MULTILINE),
},
# 3. factory.py _language_specific_guidance:
Language.RUBY: "使用 ruby-prof / stackprof 进行性能分析\n...",
# 4. tools.py — 添加 RubyDiagnosticTool(DiagnosticTool): ...
```

### 添加新 LLM 提供商

在 `cli.py` 的 `PROVIDER_REGISTRY` 中添加一条记录：

```python
PROVIDER_REGISTRY = {
    # ... 已有提供商 ...
    "新提供商": {
        "base_url": "https://api.新提供商.com/v1",
        "patterns": ["新提供商模型前缀-"],
        "display": "新提供商名称",
    },
}
```

---

## 常见问题

### Q: SmartBench 会修改我的代码吗？
**不会。** SmartBench 只分析和建议，绝不向你的项目目录写入任何内容。

### Q: 每次启动都需要重新配置 API Key 吗？
**是的，设计如此。** API Key 仅存储在内存中。当你关闭终端并启动新的
`smartbench` 会话时，需要重新输入。这确保密钥不会意外持久化到磁盘。

### Q: 如果我的项目使用多种语言怎么办？
SmartBench 按文件数量检测**主要**语言，同时列出次要语言。
代码图和诊断聚焦于主要语言。多语言混合项目会标为 `mixed`。

### Q: 能用本地 LLM 吗（Ollama、vLLM）？
可以。使用以 `llama`、`mistral`、`qwen2`、`codellama` 或 `deepseek-r1`
开头的模型名，SmartBench 会自动路由到 `http://localhost:11434/v1`。

### Q: 如果项目没有 README 怎么办？
阶段 1（确定性扫描）依然正常工作。阶段 2（LLM 项目理解）会优雅跳过。
辩论引擎将使用文件内容 + 代码图上下文替代。

### Q: 不编译怎么构建代码图？
SmartBench 使用基于正则表达式的启发式解析，零额外依赖，速度快。
可以捕获约 85-90% 的函数/类定义和调用边。
如需更高精度，可安装 tree-sitter：
```bash
pip install tree-sitter
```

---

## 更新日志

### v0.6.0（2026-06-13）— RAG 向量检索 + 证据核查引擎

- **新增**：`rag/` — RAG 向量检索模块（5 个文件）
  - 语言感知代码切块器（函数/类/文件级）
  - 三级嵌入后端自动降级（sentence-transformers → sklearn TF-IDF → 字符哈希）
  - 双后端向量存储（SimpleVectorStore 默认，ChromaDB 可选）
  - 混合检索器（图结构 + 向量语义融合）
- **新增**：`verifier/` — 证据核查引擎（6 个文件）
  - 文件:行号 存在性验证 + 模糊路径解析（Levenshtein 距离）
  - 调用链交叉验证（代码图 vs 实际源码）
  - 提案评分 + 幻觉标记（零 LLM 调用，纯磁盘 I/O）
- **增强**：辩论引擎 — Proposer → **Verifier** → Critique → **Verifier** → Judge
  - 每条提案需附带 `evidence_claims`（文件路径 + 调用链 + 代码模式）
  - 自动标记 ✓/⚠/✗，拒绝引用不存在文件的提案
- **增强**：Prompt — Proposer 强制要求证据声明，Critique/Judge 接收核查结果
- **健壮性**：Python 3.14 兼容（绕过 PyTorch DLL 崩溃，自动降级 numpy+sklearn）
- **修复**：ChromaDB C 扩展段错误、TF-IDF 维度不一致、双重 chunking 性能问题

### v0.5.0（2026-06-13）— 通用平台重构

- **新增**：`detector/` — 14 语言零 LLM 项目指纹采集
- **新增**：`prompts/` — 动态 PromptFactory，消除所有硬编码假设
- **新增**：`graph/` — 代码图引擎（12 语言，AST→图→检索）
- **新增**：`diagnostics/` — 可插拔工具注册表（8 工具，按语言路由）
- **重构**：`engine/debate.py` — 完全重写，零 Raft KV 引用
- **重构**：`cli.py` — 67KB→13KB，三种交互模式
- **体验**：API Key 掩码输入，`*` 回显 + `****abcd` 确认
- **体验**：模型名称驱动自动配置（8 个提供商）
- **修复**：全面自审查发现的 9 个 Bug（I/O 缓存、运算符优先级、类型匹配、语言检测等）

### v0.4（2026-04-13）
- 诊断引擎：崩溃、死锁、内存泄漏、性能瓶颈检测
- GDB + 火焰图 + Linux 诊断工具集成

### v0.3（2026-04-13）
- 多 Agent 辩论引擎（Proposer/Critique/Judge）
- 代码缓存 + 性能回归分析

### v0.2（2026-04-10）
- 多模型协作、权重引擎、建议聚合去重

### v0.1（2026-04-08）
- 初始版本：Raft KV 压测 + 基础分析

---

## 许可证

MIT
