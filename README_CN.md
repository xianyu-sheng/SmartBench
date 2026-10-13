# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/xianyu-sheng/SmartBench?label=release&color=4C1)](https://github.com/xianyu-sheng/SmartBench/releases)
[![Status: Public Beta](https://img.shields.io/badge/status-public_beta-orange.svg)](#项目状态)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[English](README.md) | 中文

**证据约束的代码诊断。** 静态分析拥有源码事实；LLM 只能*提出*假设；resolver 和 validator 决定假设能否绑定回真实操作、类型与控制流。绑不上的保持 `unknown` / `abstained`，永远不会被提升为 finding。

> 信任边界就是产品本身：**模型可以犯错，但它无法把一个错误的断言变成 finding。**

## 真实发现（运行时验证）

| 仓库 | 发现 | 线程 | 验证方式 |
|---|---|---|---|
| [qi4L/qscan](https://github.com/qi4L/qscan/issues/22) | `CheckSID` 在每条错误路径泄漏 `*sql.DB`（缺 `defer db.Close()`） | **OPEN** | goroutine 增量 +5/次 → 修复后归零 |
| [firefart/stunner](https://github.com/firefart/stunner/issues/89) | `testPassword` 在所有路径泄漏 TURN 连接（暴力破解累积 socket） | **CLOSED** — 修复已在 `dev` 确认，发现正确 | 静默 mock server：只有修复后服务端 Read 才到 EOF |
| [mosajjal/sniproxy](https://github.com/mosajjal/sniproxy/pull/203) | `refresh_interval: 0` 导致进程崩溃：ACL 刷新 goroutine 里 `time.NewTicker` panic | **PR OPEN** | 运行时复现 panic → 修复对照 → 测试通过 |
| [sparckles/Robyn](https://github.com/sparckles/Robyn/issues/1432) | SSE 测试用 `stream=True` 但未显式关闭响应（测试卫生） | **OPEN** | 确认有效，低严重性 |

每个上游提交都走同一条链：静态审计 → 运行时复现 → 本地修复 → 对照实验（修复前失败、修复后通过）。完整分析见 [REAL_WORLD_CASES.md](REAL_WORLD_CASES.md)。

同一检测层在 Kubernetes、etcd、Prometheus、Requests 的受控 before/after 快照上复现了 **6/6 已知并发与资源生命周期缺陷**——包括 [CVE-2026-73500](https://github.com/advisories/GHSA-6vch-q96h-7gc3)（etcd TLS listener DoS）背后的无界 goroutine 模式。

[Live demo](https://xianyu-sheng.github.io/SmartBench/) · [架构说明](docs/ARCHITECTURE.md) · [使用指南](docs/USAGE_GUIDE.md)

[![SmartBench 终端演示](docs/assets/smartbench-demo-poster.png)](https://xianyu-sheng.github.io/SmartBench/)

> [!IMPORTANT]
> SmartBench 是公测版本，不是生产级 SAST 替代品。它能做受控的仓库审计、能复现一批已知缺陷，但**尚未**建立对未知 bug 的通用 precision / recall。一份干净的报告意味着"没有受支持的发现"，而不是"仓库没有 bug"。
>
> 常规工作流是只读的：SmartBench 从不修改被分析仓库、不擅自开 issue/PR、不联系维护者——这些动作由人单独执行。

## 它做什么、不能证明什么

| 路径 | 现在的产出 | 不能证明什么 |
|---|---|---|
| 确定性 `unified` 分析 | 规则 finding、能力评估、源码角色、图事实、JSON 与 SARIF | 干净结果 ≠ 没有 bug |
| 带模型的 `quick` | 项目假设、证据门审查、显式的拒绝与弃权状态 | 结论不因引用了真实事实就被证明 |
| before/after 语料 | 可复现地验证分析器能区分 20 个已知修复 | 未知 bug 的 precision / recall |

当前的目标用户是评估这套架构的人，或做受控、人工复核式仓库审计的人——不是把它当 CI 质量门。

## 快速开始

需要 Python 3.10+ 和 Git。

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[graph]"
```

**确定性分析（不用 LLM）：**

```bash
smartbench unified run --project /path/to/repo --output report.json --sarif report.sarif

# 让 CI 按 finding 退出（默认无论有没有发现都退出 0）
smartbench unified run --project . --fail-on warning

# 字节级稳定的 JSON，适合 golden file / diff
smartbench unified run --project . --output report.json --deterministic-output
```

`--fail-on` 取值 `none`（默认）/ `info` / `warning` / `error`。退出码：`0` 未达阈值、`1` 达到阈值、`2` 参数或内部错误。

**证据门约束的 LLM 审查：**

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick --project /path/to/repo \
  --concern "find correctness and resource-lifecycle risks" \
  --output agent-report.json
```

`quick` 走完整融合管线：`AnalysisSession` → 确定性规则 → ProjectReader 假设 → 证据 resolver → Proposer → 源码验证器 → Critique → Judge，全程由证据门约束。

**提交上游前先查活跃分支。** 默认分支上的有效发现，可能在 `dev`/`develop`/`next`/`release` 分支上早已修好：

```bash
smartbench check-branches --input agent-report.json --repo /path/to/repo
```

返回 `Already-fixed` 就意味着**不要提交**；`--json --output file.json` 是给脚本用的机器可读形式。检查器会跨分支比对清理模式、做函数级 diff 和 commit pickaxe（`git log -S`）；浅克隆只拉缺失活跃分支的 tip。

**可选扩展：** `python -m pip install -e ".[graph,rag]"` 会加上本地向量索引；不装也不影响确定性的图检索。

## 工作原理

```text
仓库
  -> ScanPlan -> 语言前端 -> SemanticIR -> SemanticLinker
  -> AnalysisSession
       |-> 确定性规则与声明式状态分析器
       |-> 确定性 GraphRAG
       |-> ProjectReader 假设
             -> 证据 resolver -> validator -> CFG 生命周期分析器
       `-> EvidencePack { facts, hypotheses, 源码引用 }
             -> Proposer -> 源码验证器 -> Critique -> Judge
  -> JSON / SARIF / 基准报告
```

所有主入口共享同一个 `AnalysisSession`：仓库发现、语言降级、IR 构建和语义链接只运行一次。

| 命令 | LLM 行为 |
|---|---|
| `smartbench unified run` | 默认无 LLM，只用确定性规则 |
| `smartbench quick` | 可选 ProjectReader + 证据约束审查 |
| `smartbench benchmark run` | 无 LLM；固定快照与声明式期望 |
| `smartbench eval-rag` | 无 LLM；在会话 IR 上评估检索 |
| `smartbench diagnose` | 本地编译器/进程/工具探测，不是语义分析 |

`smartbench/analysis/` 里的语言中立分析器只处理规范化操作、**从不 import 语言解析器**：资源生命周期（acquire → cleanup，带 CFG dominator 检查）、声明式状态机、控制流/有界 ICFG、函数内污点传播、以及源码可证的 typed evidence。

## 语言支持

| 语言 | 当前级别 | 边界 |
|---|---|---|
| Python | 语义级（最深） | CFG/ICFG、调用、状态规则、基于类型注解的表层类型证据 |
| Go | 语义级（最深） | CFG/ICFG、状态/资源分析、表层 + `go/types` 证据（`tools/typeprobe`） |
| JavaScript / TypeScript | 语义级（部分） | 常见语句、调用和控制流；async、异常和动态分派仍不完整 |
| Rust | 结构级 | Tree-sitter 符号和图上下文；没有完整语义降级 |
| Java、Kotlin、C/C++、Ruby、Swift、C#、Zig | 检测 / 启发式 | 指纹识别与兜底结构，不是编译器级分析 |

语言缺口已开成可认领 issue：[#6](https://github.com/xianyu-sheng/SmartBench/issues/6)（中间层）、[#7](https://github.com/xianyu-sheng/SmartBench/issues/7)（共享降级脚手架）、[#8](https://github.com/xianyu-sheng/SmartBench/issues/8)（可执行一致性套件）。新语义前端必须提供稳定位置、显式能力声明、确定性 IR 序列化和至少一个 before/after 基准。

## 证据边界

`EvidencePack` 区分两类输入：

- `facts` — 源码可证的图/分析器事实，带稳定 `fact-*` ID；
- `hypotheses` — ProjectReader 解读和启发式候选，带 `hypothesis-*` ID。

假设对后续 Agent 可见（用于决定查什么），但证据门**永不**把假设 ID 当事实接受。最终的具名结论必须引用有效 fact ID；证据缺失、模糊或冲突时保持 `unknown` / `abstained`。

**当前门校验的是 fact ID 是否存在，不是逻辑蕴含。** 模型仍可能引用一个不能支持其结论的真实事实。位置校验、确定性 ProjectReader 验证和人工复核是彼此独立的要求；带类型的"结论—证据"关系仍是未完成工作。

ProjectReader 不写 `SemanticIR`。在已实现的资源生命周期路径上，它选出一个真实调用并提出项目级清理协议；随后确定性阶段会绑定操作与类型证据、拒绝零/多重结构匹配、校验绑定与可达性、执行语言中立 CFG dominator 检查，最后要么产出 finding，要么显式弃权。只有在确定性拒绝之后，才允许一次有界修复。

## 读报告

| 状态 | 含义 |
|---|---|
| `full` | 相关前端满足该规则声明的全部要求 |
| `partial` | 运行了文档化的近似实现 |
| `unsupported` | 所需语义不可用——这不是干净结果 |
| `unknown` | 规则未声明足够要求，无法声称覆盖 |
| `abstained` | 证据缺失、模糊、冲突，或存在所有权转移的可能 |

三个必须知道的注意事项：

- `verified` / `hallucinated` 描述的是源码位置和结构引用检查，**不证明 bug 结论正确**；
- `consensus_reached` 只表示 Proposer、Critique、Judge 都返回了 schema 合法输出——是阶段完成标志，不是独立模型间的统计一致性；
- Critique/Judge 失败时，Proposer/Judge 的输出保留在 `unreviewed_suggestions` 供审计，但不会被提升为 `final_suggestions`；控制台会明说"审查不完整"，不会伪装成干净结果。

## 评测：数字意味着什么

**回归语料 —— 36/36。** 20 个 before/after 案例（36 个最小快照），来自公开修复（Requests、FastAPI、Prometheus、Kubernetes、Gin、Terraform）与 SmartBench 自己的发现（qscan、stunner、sniproxy），外加 9 个合成模式固件。每个"有 bug"快照产生预期规则，每个"已修复"快照不产生告警。这证明的是**表达能力**，不是未知 bug 召回率。

```bash
smartbench benchmark run --manifest benchmarks/real/manifest.yaml --output benchmark-report.json
```

**真实仓库扫描 —— 诚实数字。** 在 12 个开源项目（Python 与 Go）上运行后，410 条 finding 经过人工核验，再对照 SDK 类型和项目文档交叉检查。结果值得在信任任何数字之前先读一遍：

- 确定性规则在这些仓库上产出 **0 个真 bug**（它们是保守启发式，不会在干净代码上幻觉）；
- 最初提交上游的 4 条发现里，**3 条是错的**并已在上游更正（把 `io.Reader` 当成 `io.ReadCloser`；两条 PocketBase 断言与其文档化的 fail-stop 设计和立即返回 nil 的 `Close()` 矛盾）；
- 1 条有效观察幸存（Robyn 的测试卫生）；
- 主要失败模式的根因是：**验证器无法跨依赖边界解析类型**。

最后一点直接催生了类型检查适配器（`tools/typeprobe` + `GoTypeCheckerProvider`）。加上之后：Go 仓库产出 40,650 条 `TYPE_CHECKER` 证据、解析出 2,453 个更近的类型，LLM 建议的内部验证率从 4/18（22%）升到 13/16（81%）。但独立交叉复核显示：仅剩的两条 `resty` 发现**仍然都是误报**——内部验证衡量的是"断言是否落到了真实证据上"，不是真实 bug 精度。每条候选在提交前仍需跨依赖复核。（这条教训记录在 [docs/TYPE_EVIDENCE_LESSON_CN.md](docs/TYPE_EVIDENCE_LESSON_CN.md)。）

## 已知限制

- 多数内置规则仍是源码启发式；声明式状态规则和资源生命周期分析器是最强的语义样例。
- 异常流、异步调度、动态分派、别名分析、goroutine happens-before、跨函数 channel alias 都不完整。
- ProjectReader 生命周期分析器目前只证明规范化 defer 风格的清理。
- Python 前端会扁平化 `with`-context 清理，因此 IR 中暂时无法区分 context-manager guard 与裸 acquire 调用。
- 大仓库的延迟与内存还没有公开预算。
- 本地向量/TF-IDF 缓存不能跨所有可选依赖变化复用；如果缓存是用 scikit-learn 建的、之后在没装它的环境打开，请删除仓库的 `.smartbench/` 缓存。
- 通过 `quick` 发送的仓库内容对配置的远端模型提供商可见。
- 干净报告意味着"没有受支持的发现"，不是"仓库没有 bug"。

最大的几个缺口已开成可认领 issue——见[路线图](https://github.com/xianyu-sheng/SmartBench/issues/14)及其链接的各领域 issue（语言语义、度量、接口）。

## 安全

- 源码路径限制在仓库根目录内；外部符号链接和 `../` 逃逸会被忽略。
- 外部命令不经过 shell，且有时间和输出上限。
- 可选的 `quick --sandbox` 只把补丁应用到临时副本，但仓库测试仍以当前用户权限运行。
- 不要把含密钥或受限源码的仓库发给远端提供商，除非你能接受这种暴露。
- 没有反复验证和人工决策，不要把 finding 提交到上游。

## 开发

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI 覆盖 Python 3.10–3.12 测试、解析器适配器检查、36 快照基准、ProjectReader 边界实验和 clean-wheel CLI 冒烟。贡献规则（含必填的 CI 证据段）见 [CONTRIBUTING.md](CONTRIBUTING.md)。

文档：[架构](docs/ARCHITECTURE.md) · [使用指南](docs/USAGE_GUIDE.md) · [真实案例](REAL_WORLD_CASES.md) · [历史基准语料](benchmarks/real/README.md) · [ProjectReader 盲测实验](benchmarks/experiments/project_reader_blind/README.md)

## 项目状态

SmartBench 适合受控的仓库实验、架构研究和作品集展示。它已在真实项目产出运行时验证的 bug，并交付了 20 案例基准语料和面向资源生命周期、状态机、数据流的语言中立分析器。

接下来该做的是**度量，而不是更多表面功能**：

1. 在扩展语料上公开 precision、recall、弃权率、试验稳定性、延迟与成本（[#10](https://github.com/xianyu-sheng/SmartBench/issues/10)、[#11](https://github.com/xianyu-sheng/SmartBench/issues/11)、[#12](https://github.com/xianyu-sheng/SmartBench/issues/12)）；
2. 在不削弱 IR 边界的前提下加深语言语义（异常流、异步、别名、并发）；
3. 降低语言接入成本（[#6](https://github.com/xianyu-sheng/SmartBench/issues/6)–[#8](https://github.com/xianyu-sheng/SmartBench/issues/8)）并暴露公共 API（[#9](https://github.com/xianyu-sheng/SmartBench/issues/9)）。

## License

[MIT](LICENSE)
