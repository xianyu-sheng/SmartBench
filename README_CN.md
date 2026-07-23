# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Version: 0.6.1](https://img.shields.io/badge/version-0.6.1-4C1.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#项目状态)

面向本地代码仓库、强调证据可追溯的代码诊断工作台。

[English](README.md) · [使用指南](docs/USAGE_GUIDE.md)

SmartBench 将确定性仓库指纹、代码结构解析、可选 RAG 检索、三角色 LLM 审查、本地诊断探针和磁盘证据核验组合在一起。它不会编辑目标仓库的源文件；启用 RAG 时会在 `<project>/.smartbench/` 下写入索引缓存。

> SmartBench 目前处于 Beta 阶段。证据核验能够确认引用的文件、行号、符号以及部分调用关系，可减少虚构引用，但不能证明每条诊断在语义上必然正确。

## 当前已经实现

- 通过依赖目录剪枝与明确扫描上限，确定性识别语言、框架、构建系统、入口、依赖和 Git 信号。
- 安装 `graph` 可选依赖后，对 Python、Go、JavaScript、TypeScript 和 Rust 使用 tree-sitter 提取符号。
- 其他语言使用正则启发式回退，并保守提取近似调用关系；同名定义存在歧义时不会虚构调用边。
- Proposer、Critique、Judge 三个审查角色，可共用模型，也可分别配置模型。
- 对引用路径、行号、符号和部分调用链做确定性核验；模糊路径修正会明确标记为“部分可信”。
- 可选代码图与本地向量混合检索，并带有标注过的检索评测样例。
- 按策略执行 Python、Go、C/C++、Java/Kotlin 及通用系统诊断探针或生成工具建议。
- 混合语言仓库会按全部已检测语言路由本地诊断，不会退化成空的通用结果。
- 支持 Git URL、worktree 与单仓库子项目识别，并限制外部命令的执行时间和输出量。
- 支持输出 JSON 报告，便于非交互流程使用。

## 工作流程

```text
代码仓库
   │
   ├─ 确定性指纹 ── 语言 / 框架 / 构建 / Git 信号
   │
   ├─ 代码结构图 ── tree-sitter 符号 + 启发式回退
   │              │
   │              └─ 可选本地 RAG 索引
   │
   ├─ 按策略选择的本地诊断探针
   │
   └─ Proposer → 证据核验 → Critique → 证据核验 → Judge
                                             │
                                             └─ 带位置和评分的诊断结果
```

缺失文件或非法行号会被标记为虚构引用；模糊匹配到的路径只会得到部分可信结论。语义正确性仍需要测试、编译器或 Linter 输出、性能数据或人工审查确认。

## 语言覆盖

仓库指纹可识别 Python、Go、Rust、C、C++、Java、Kotlin、JavaScript、TypeScript、Ruby、Swift、C# 和 Zig，也能识别混合语言项目。

可选 tree-sitter 后端目前覆盖 Python、Go、JavaScript、TypeScript 和 Rust。其他语言使用启发式结构解析；C 目前只有文件级发现。回退图适合上下文检索，但不是编译器级分析。

## 快速开始

要求 Python 3.10 或更高版本，并安装 Git。

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
smartbench --version
smartbench --help
```

安装五种语言的 tree-sitter 精确解析器：

```bash
python -m pip install -e ".[graph]"
```

启动交互式向导：

```bash
smartbench
```

通过环境变量配置模型，运行快速诊断并保存报告：

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project . \
  --concern "检查正确性和并发风险" \
  --output report.json
```

## 统一多语言诊断

SmartBench 提供快速的统一诊断框架，支持多语言静态分析：

```bash
# 列出可用的诊断规则
smartbench unified rules

# 列出支持的语言
smartbench unified languages

# 运行统一诊断
smartbench unified run --project .

# 只运行指定规则和扫描指定语言
smartbench unified run --project . --rule null_dereference --rule hardcoded_secret --language python

# 导出 SARIF 报告（用于 GitHub/GitLab 代码扫描集成）
smartbench unified run --project . --sarif report.sarif --output report.json
```

检查本机探针，或跳过 LLM 辩论执行诊断路径：

```bash
smartbench check
smartbench diagnose --project . --perf --output diagnostics.json
smartbench diagnose --project . --perf --system-probes
smartbench eval-rag --project . --queries tests/fixtures/rag_eval_queries.json
```

对于可信仓库，可以使用 `smartbench quick --project . --sandbox`：Judge 会尝试
提供 unified diff，SmartBench 只在临时副本中应用有效补丁，先确认基线测试通过，
再运行补丁后的同一组测试。只有自然语言建议、没有补丁时会标记为“跳过”，不会误报
为“已验证”。

凭证环境变量包括 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GLM_API_KEY`、`DOUBAO_API_KEY`、`MOONSHOT_API_KEY` 和 `DASHSCOPE_API_KEY`。Anthropic 使用原生 Messages 协议，其余供应商使用各自的 OpenAI 兼容聊天端点。快速模式的默认模型可通过 `SMARTBENCH_<PROVIDER>_MODEL` 覆盖，例如 `SMARTBENCH_ANTHROPIC_MODEL`。向导输入的 Key 只保存在当前进程内存中。

每个辩论角色的输出只有在 JSON 对象符合所需结构后才会被接受；格式错误或字段形状错误会重试，无效的 Judge 输出不会被标记为“达成共识”。`--output` 使用原子写入，保存失败时 CLI 返回非零状态码。

## 可选 RAG

```bash
python -m pip install -e ".[rag]"
```

未安装可选依赖时，SmartBench 会使用仅代码图检索。启用后，本地向量存储会在被分析仓库的 `.smartbench/` 目录中写入索引；如有需要，请将该目录加入目标仓库的忽略规则。索引会剪枝依赖与缓存目录，并默认限制为最多 2,000 个文件、单文件 2 MB、10,000 个分块。

## 安全边界

- 常规诊断流程不会编辑被分析仓库的源文件。
- Git URL 会以非交互方式克隆到临时目录，并在 SmartBench 退出时清理。
- 扫描、检索和证据验证只读取解析后仍位于项目根目录内的常规文件；外部符号链接和 `../` 越界路径会被忽略。
- 仓库元数据、README、源码、日志、工具输出和前序模型输出会在提示词中标记为不可信数据，其中类似指令的文字不应控制工作流。这只能缓解 Prompt Injection，并非形式化隔离边界；不要分析包含不允许发送给所配置模型供应商之秘密的仓库。
- Git remote 中的凭证以及 URL query/fragment 会在写入项目指纹或显示前移除。
- 项目级诊断可能在目标路径内执行本机已安装的编译器或分析器，只应分析你信任的仓库。
- 外部工具不通过 shell 执行，并受到时间和输出上限约束；在 POSIX 系统中，超时会终止所启动的整个进程组。
- 主机进程、内存和内核探针（`ps`、`vmstat`、`dmesg`）默认关闭；只有显式使用 `diagnose --system-probes` 才会运行，并且输出可能暴露主机信息。
- `--sandbox` 必须显式开启。它会保护工作区、限制补丁只能修改声明的目标文件，并移除凭证型环境变量，但不是操作系统安全边界；仓库测试仍拥有当前用户权限，也可能访问网络或用户文件。
- 证据状态只描述“引用是否有依据”，不等价于漏洞或修复已被形式化证明。

## 项目结构

```text
smartbench/
├── cli/             命令、交互向导、诊断阶段与展示
├── detector/        确定性仓库指纹
├── graph/           tree-sitter 适配、回退结构图与图检索
├── rag/             可选分块、嵌入、向量检索与评测
├── engine/          Proposer / Critique / Judge 编排
├── verifier/        文件、行号、符号和调用链核验
├── diagnostics/     本地诊断工具注册表与策略执行器
├── llm/             供应商配置与模型调用
└── prompts/         上下文感知结构化提示词
```

早期面向 Raft 的实现保存在 `legacy/` 中，仅供历史参考，并已从发布包中排除。

## 开发与验证

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI 会在 Python 3.10、3.11、3.12 上执行 lint、编译检查和测试，单独验证五种 tree-sitter 适配器，构建 wheel 与源码包，并在干净环境安装 wheel、从源码目录外执行 CLI 冒烟测试。

## 验证快照

0.7.0 发布候选版本于 2026-07-23 完成以下验证：

- 强制启用五种 tree-sitter 适配器时，468 项自动化测试全部通过。
- Ruff、字节码编译、wheel 和源码包构建通过。
- 在干净 Python 3.12 环境安装 wheel 后，从源码目录外成功运行 `--help`、`check`、`diagnose`、仅代码图 `eval-rag`，以及新的 `unified` 系列命令。
- 本仓库 12 条查询的仅代码图回归样例达到 MRR 0.829、Hit@5 100%。该结果只用于自检，不代表通用诊断准确率。
- 在 SmartBench 和 Xenon 两个真实项目上验证了统一诊断与 SARIF 报告输出。

版本详情见 [CHANGELOG](CHANGELOG.md)。

## 项目状态

SmartBench 的定位是诊断工作台，不替代编译器、Linter、安全扫描器、Profiler 或人工审查。接下来的质量里程碑是：

1. 将检索与诊断精度评测扩展到独立的带标签仓库。
2. ✅ 输出 SARIF 等标准机器可读审查格式（已实现）。
3. 为可选仓库测试执行增加更强的进程隔离。
4. 扩展机器可应用补丁覆盖率和语言专项验证。

## 许可证

[MIT](LICENSE)
