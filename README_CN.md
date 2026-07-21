# SmartBench

**通用 AI 代码诊断平台** — 多 Agent 辩论 + 证据验证 + 工具执行 = 可信任的诊断报告。

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-266%20passed-brightgreen.svg)]()
[![Ruff](https://img.shields.io/badge/ruff-clean-cyan.svg)]()

**SmartBench 从不修改你的代码。** 只分析、诊断、建议 — 你始终在掌控之中。

---

## SmartBench 是什么？

SmartBench 是一个 **LLM 驱动的代码诊断工具**，通过结构化的多 Agent 辩论来分析任意代码库，并以**零幻觉证据验证**保障每个诊断结论的可信度。

AI 的每一条声称都必须引用精确的文件路径和行号 — 这些声明在到达最终报告之前会经过磁盘验证。

```
$ smartbench
╔══════════════════════════════════════════════╗
║ SmartBench — Universal Code Diagnosis       ║
║ AI-powered analysis for any codebase        ║
╚══════════════════════════════════════════════╝

Step 1/4 — 项目在哪？
Step 2/4 — 配置 LLM API Key
Step 3/4 — 分析项目中...
Step 4/4 — 你想诊断什么？

[Proposer] → [Verifier] → [Critique] → [Judge] → 报告
```

## 快速开始

```bash
pip install -e .
export DEEPSEEK_API_KEY=sk-your-key

smartbench              # 交互式 4 步向导
smartbench --quick      # 自动检测一切
smartbench check        # 查看可用诊断工具
```

### CLI 命令

| 命令 | 说明 |
|---|---|
| `smartbench` | 交互式向导：项目 → API Key → 检测 → 诊断 |
| `smartbench --quick` | 非交互模式，使用环境变量中的 API Key |
| `smartbench quick --project ./my-repo` | 对指定项目快速诊断 |
| `smartbench diagnose --project ./my-repo --symptoms "响应慢"` | 针对性诊断 |
| `smartbench diagnose --project ./my-repo --output report.json` | 导出 JSON 报告 |
| `smartbench check` | 查看当前目录可用的诊断工具 |

## 核心原理

### 五阶段流水线

```
Phase 1: 项目指纹（零 LLM）→ 语言/框架/构建系统检测
Phase 2: LLM 理解 README → 高层次项目认知
Phase 3: 策略选择 → 从 5 种策略中自动选择
Phase 4: 代码图 + RAG 索引 → AST 解析 + 向量化
Phase 5: 多 Agent 辩论 → Proposer → Critique → Judge → 最终报告
```

### 反幻觉保障

每条诊断结论经过**三层验证**：

1. **文件存在性** — 声明的文件路径在磁盘上检查
2. **行号准确性** — 引用的行号必须匹配实际源码
3. **调用链完整性** — 函数调用关系通过代码图验证

验证失败的声明会被**标记并降级**，不会进入最终报告。

### 多 Agent 辩论引擎

| 角色 | 职责 |
|---|---|
| **Proposer（方案提出者）** | 分析代码上下文，提出带精确文件路径和行号的修复方案 |
| **Critique（交叉审查者）** | 对抗性审查 — 寻找反例、遗漏上下文、误报 |
| **Judge（最终仲裁者）** | 综合辩论记录，产出带共识评分的最终报告 |

## 技术架构

```
smartbench/
├── cli/             # CLI（104 行 main + wizard/phases/display）
├── llm/             # Provider 注册 + API 客户端（8 个 provider，重试逻辑）
├── detector/        # 零 LLM 项目指纹识别
├── graph/           # AST 代码图（tree-sitter + 正则），14 语言
├── rag/             # 向量索引（3 层：transformers → TF-IDF → 字符哈希）
├── verifier/        # 证据验证（磁盘 I/O，零 LLM）
├── engine/          # 多 Agent 辩论引擎
├── diagnostics/     # 30+ 可插拔诊断工具
└── prompts/         # 动态 Prompt 工厂（语言专项指导）
```

## 支持的语言和框架

**14 种语言**：Python · Go · Rust · C · C++ · Java · Kotlin · JavaScript · TypeScript · Ruby · Swift · C# · Zig · 混合项目

**Tree-sitter 精确解析**：Python、Go、JavaScript、TypeScript、Rust（其他语言正则兜底）

**20+ 框架自动检测**：FastAPI · Flask · Django · Gin · Echo · Fiber · Express · NestJS · Next.js · React · Vue · Spring Boot · Axum · Actix · gRPC 等

## 5 种诊断策略

| 策略 | 关注点 | 触发工具 |
|---|---|---|
| `performance_analysis` | CPU、内存、I/O 分析 | py-spy、pprof、perf |
| `correctness_audit` | Bug 检测、边界情况 | ruff、mypy、go vet |
| `architecture_review` | 设计模式、耦合度 | 代码图循环依赖检测 |
| `security_scan` | 注入、密钥暴露 | bandit、gosec、npm audit |
| `hotspot_analysis` | 高变更文件、复杂度 | git log + 代码图 |

## 8 个 LLM Provider

从模型名自动检测。支持角色级路由：不同模型分别担任 Proposer / Critique / Judge。

DeepSeek · OpenAI · Anthropic · GLM · 豆包 · Moonshot · 通义千问 · Ollama（本地）

## 可选依赖

```bash
pip install -e ".[dev]"     # pytest, ruff
pip install -e ".[graph]"   # tree-sitter（精确 AST 解析）
pip install -e ".[rag]"     # sentence-transformers + ChromaDB
```

## 常见问题

**Q: SmartBench 会修改我的代码吗？**
不会。只读分析。输出写入独立报告文件。

**Q: 如何防止 AI 幻觉？**
LLM 声称的每个文件路径和行号都经过零 LLM 验证器在磁盘上确认。幻觉声明会被标记和降级。

**Q: 能在 CI 中使用吗？**
支持非交互模式：`smartbench quick --project . --output report.json`

**Q: 需要 GPU 吗？**
不需要。嵌入引擎自动降级（sentence-transformers → TF-IDF → 字符哈希）。LLM 调用走远程 API。完全 CPU 兼容。

**Q: 成本多少？**
取决于 LLM provider。本地（Ollama）：免费。DeepSeek：约 $0.01/次。一次典型诊断产生 4 次 LLM 调用（策略选择 + 3 轮辩论）。

## License

[MIT](LICENSE) © Xianyu Sheng
