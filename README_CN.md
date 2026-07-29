# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Version: 0.7.0](https://img.shields.io/badge/version-0.7.0-4C1.svg)](CHANGELOG.md)
[![Status: Public Beta](https://img.shields.io/badge/status-public_beta-orange.svg)](#项目状态)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**让 LLM 提出语义假设，让确定性分析决定证据是否成立。**

[English](README.md) · [▶ 观看 2 分 39 秒实战演示（MP4）](https://raw.githubusercontent.com/xianyu-sheng/SmartBench/main/docs/assets/smartbench-demo.mp4) · [演示时间线](docs/DEMO_3_MINUTES_CN.md) · [架构文章](docs/EVIDENCE_LOOP_ARTICLE_CN.md) · [使用指南](docs/USAGE_GUIDE.md)

[![观看 SmartBench 分析完整 Requests 仓库](docs/assets/smartbench-demo-poster.png)](https://raw.githubusercontent.com/xianyu-sheng/SmartBench/main/docs/assets/smartbench-demo.mp4)

**[▶ 直接播放或下载 H.264 MP4](https://raw.githubusercontent.com/xianyu-sheng/SmartBench/main/docs/assets/smartbench-demo.mp4)**

*这段 2 分 39 秒的 Ubuntu GNOME 无声原生录屏从打开新终端开始，现场输入 `smartbench`，导入处于公开修复前提交的完整 Requests 仓库，构建代码图与 RAG 索引，并展示 Proposer → Verifier → Critique → Judge。它不是预存报告回放；视频中不包含 API Key 或音轨。*

SmartBench 是面向本地代码仓库、强调证据可追溯的语言无关代码诊断工作台。它组合 SemanticIR、CFG/ICFG 与状态分析、确定性图检索，以及受证据约束的多 Agent。常规诊断只读，不修改目标仓库，也不会自动联系上游项目。

> [!IMPORTANT]
> SmartBench 当前是公开 Beta，不是生产级 SAST。证据引用能够证明“信息来自哪里”，但不能自动证明每条诊断必然正确。无法覆盖的语义会保留为 `unknown`、`partial` 或 `abstain`，不会被当成干净结果。

## 与普通 LLM 代码审查有什么不同

SmartBench 不让模型同时解释项目、创造事实并裁决自己的证据：

```text
代码仓库
  → 语言前端 → SemanticIR
  → 确定性 inventory / GraphRAG EvidencePack
  → Agent 语义假设
  → 唯一匹配 evidence resolver
  → 原始 validator
  → CFG / ICFG / 状态分析
  → Finding，或明确 abstain
```

Agent 可以选择 operation、result position、cleanup method、member path 或类型假设，但不能向 SemanticIR 写入事实。`fact-*` 和 `type-*` ID 由 resolver 绑定：

- 恰好一个结构匹配：`resolved`；
- 没有匹配：`unresolved` 并 abstain；
- 多个匹配：`ambiguous` 并 abstain；
- 旧客户端提交的 ID 只进入审计区，不能覆盖确定性证据。

语义选择被拒绝后，bounded repair 只能看到同一份 blind inventory、上一次结构化输出和确定性拒绝原因；它看不到历史目标文件和 before/after 答案，replacement model 仍需通过同一个 resolver 与 validator。

## 当前证据，而不是宣传口号

截至 **2026-07-29** 的可复现快照：

| 证据 | 结果 | 能够说明什么 |
| --- | ---: | --- |
| 安装 graph extras 的测试套件 | **586 passed** | 前端、契约、resolver、分析器、报告与 CLI 回归 |
| 历史公开 before/after corpus | **12/12 snapshots passed** | 六个缺陷快照命中声明规则，六个修复快照不命中 |
| DeepSeek blind resolver A/B | **6/6 trials passed** | 两个排除目标文件、有独立 reference 的 Go 协议无需 repair 即可复现 |
| 同一 A/B 的独立负样本 | **0 findings** | 已接受协议没有在干净负例上触发 |
| Blind unsupported 案例 | **2/4 cases** | Gin、Terraform 缺少合法 reference，系统选择 abstain |

`6/6` 只代表两个 Go 资源协议、每个三轮的真实实验，不是通用 Bug 检测准确率。历史 corpus 证明系统能够确定性表达已知缺陷，不证明未知 Bug recall。

## 离线证据闭环演示

无需 API Key 和网络：

```bash
python -m smartbench.experiments.evidence_loop_demo \
  --output /tmp/smartbench-evidence-loop-demo.json
```

脚本化 Agent 会故意提出错误 cleanup method，终端随后展示：

```text
初始假设被 gate 拒绝
  → 一次 bounded repair
  → evidence resolved
  → validator supported
  → cfg_dominance_between_acquire_and_use witness
  → before=1 / after=0 / negative=0
```

这是离线机制演示，不冒充真实模型效果。真实 LLM trials 单独报告。

运行第二语言、第二类别的安全案例：

```bash
smartbench benchmark run \
  --manifest benchmarks/real/requests_proxy_authorization_guard/manifest.yaml \
  --output /tmp/requests-security.json
```

它通过 Python SemanticIR 和语言无关的 `call → guard → assign` 状态不变量验证 Requests `GHSA-j8r2-6x86-q33q`，预期为 `before=1 / after=0`。

## 当前能力

- Python、Go 具有最深的规范化 operation 与控制流支持；JavaScript/TypeScript 共享 partial SemanticIR 前端。
- CFG、有限 ICFG、保守调用/数据链接、声明式状态不变量、资源生命周期与 provenance 分析。
- content-addressed EvidencePack、图快照哈希、唯一匹配 resolver，以及显式 unknown/ambiguous。
- ProjectReader 假设与证据独占的 Proposer、Critique、Judge；无效输出不会被标记为共识。
- JSON、SARIF、benchmark、capability status、repository zone、source role 和 semantic/heuristic 标签。
- 混合语言识别、有界文件发现、Git URL worktree、依赖剪枝与路径约束。

## 语言覆盖

| 语言 | 当前层级 | 明确边界 |
| --- | --- | --- |
| Python | 深层语义 | CFG/ICFG、调用、状态规则，部分数据/类型语义 |
| Go | 深层语义 | CFG/ICFG、状态/资源分析、表层 TypeEvidence；不等同于 `go/types` |
| JavaScript / TypeScript | 部分语义 | 共享语句/调用/控制流；异步、异常、动态分派仍是 partial |
| Rust | 结构级 | tree-sitter 符号和图上下文，没有完整语义降低 |
| Java、Kotlin、C/C++、Ruby、Swift、C#、Zig | 识别/启发式 | 项目指纹和回退结构，不是编译器级分析 |

增加语义语言时只应实现 frontend contract；语言无关分析器不能导入该语言 parser。新前端需要稳定位置、显式 capability、确定性 IR 序列化和至少一个 before/after benchmark。

## 安装与仓库分析

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[graph]"

smartbench unified run \
  --project /path/to/repository \
  --output report.json \
  --sarif report.sarif
```

可选 Agent 审查：

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project /path/to/repository \
  --concern "检查正确性、状态与并发风险" \
  --output agent-report.json
```

支持 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GLM_API_KEY`、`DOUBAO_API_KEY`、`MOONSHOT_API_KEY` 和 `DASHSCOPE_API_KEY`。Key 只保存在进程内存，不写入报告。

## 如何理解结果

| 状态 | 含义 |
| --- | --- |
| `full` | 相关语言满足规则声明的全部 capability |
| `partial` | 执行了有明确边界的保守近似 |
| `unsupported` | 所需能力不可用，不能当成干净结果 |
| `unknown` | 规则没有声明足够语义要求，无法声称覆盖 |
| `abstain` | 证据缺失、冲突、歧义或存在 ownership transfer |

Finding 还会记录 source role、repository zone 和 `semantic`/`heuristic` 派生方式。

## 历史公开 corpus

| 项目 | 语言 | 公开修复 | 类别 |
| --- | --- | --- | --- |
| Requests | Python | [GHSA-j8r2-6x86-q33q](https://github.com/advisories/GHSA-j8r2-6x86-q33q) | 安全状态 guard |
| FastAPI | Python | [#5465](https://github.com/fastapi/fastapi/pull/5465) | 资源生命周期 |
| Prometheus | Go | [#1070](https://github.com/prometheus/prometheus/pull/1070) | 资源生命周期 |
| Kubernetes | Go | [#29495](https://github.com/kubernetes/kubernetes/pull/29495) | 资源生命周期 |
| Gin | Go | [#4422](https://github.com/gin-gonic/gin/pull/4422) | 资源生命周期 |
| Terraform | Go | [#38585](https://github.com/hashicorp/terraform/pull/38585) | 资源生命周期 |

```bash
smartbench benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

每份 manifest 都记录上游仓库、commit、预期行为、rule ID 和 fixture 边界。

## 安全边界

- 常规诊断不会修改仓库、创建 Issue/PR 或联系维护者。
- 仓库内容会被标记为不可信 prompt 数据，但这不是形式化 sandbox。
- 外部 symlink 和 `../` 越界路径会被忽略；外部命令不通过 shell，且有时间/输出上限。
- 本地诊断可能运行已安装的编译器或分析器，只应分析可信仓库。
- `quick --sandbox` 只在临时副本中应用补丁，但测试仍拥有当前用户的系统权限。
- 除非确认允许发送给远程 provider，否则不要分析包含秘密的仓库。

## 文档与开发

- [架构](docs/ARCHITECTURE.md)
- [使用指南](docs/USAGE_GUIDE.md)
- [实战演示时间线](docs/DEMO_3_MINUTES_CN.md)
- [双架构文章](docs/EVIDENCE_LOOP_ARTICLE_CN.md)
- [Blind transfer 实验](benchmarks/experiments/project_reader_blind/README.md)
- [历史 corpus](benchmarks/real/README.md)

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

## 项目状态

SmartBench 已适合受控真实仓库审计、秋招展示和架构研究，但还不是通用未知 Bug 检测器或生产级 SAST。下一阶段会优先：

1. 审计 5–8 个独立仓库，公开 verified、candidate 与 abstained 结果；
2. 将 blind corpus 扩展到 10–20 个外部 before/after 与自然负样本；
3. 增加第二种 Agent-discovered protocol 和另一个语义语言案例；
4. 测量 precision、recall、abstention、trial stability、延迟和成本；
5. 在不削弱 IR 边界的前提下深化异常、异步、类型、别名和并发语义。

任何 SmartBench finding 在向上游提交前，都必须经过多次验证和明确的人类决策。

## 许可证

[MIT](LICENSE)
