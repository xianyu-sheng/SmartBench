# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/xianyu-sheng/SmartBench?label=release&color=4C1)](https://github.com/xianyu-sheng/SmartBench/releases)
[![Status: Public Beta](https://img.shields.io/badge/status-public_beta-orange.svg)](#项目状态)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

SmartBench 是一个实验性的代码诊断工作台：用规范化静态分析提供事实，
用受证据约束的 LLM 完成项目语义解释与交叉审查。它是在真实开源项目中
发现并[提交多个上游 Bug](#上游提交) 的工具——每个 Bug 都经过本地运行时
复现、修复与验证后才提交。

[English](README.md) · [实战演示](https://xianyu-sheng.github.io/SmartBench/) · [架构文档](docs/ARCHITECTURE.md) · [使用指南](docs/USAGE_GUIDE.md)

[![SmartBench 终端演示](docs/assets/smartbench-demo-poster.png)](https://xianyu-sheng.github.io/SmartBench/)

常规流程**只读**。SmartBench 不会自动修改目标仓库、创建 Issue/PR 或
联系维护者——这些动作由人工单独执行。

> [!IMPORTANT]
> SmartBench 当前是公开 Beta，不是生产级 SAST 替代品。它可以用于受控
> 仓库审计，并能复现一组已知历史缺陷；目前没有证明通用未知 Bug 的
> precision 或 recall。

---

## 目录

- [SmartBench 正在验证什么](#smartbench-正在验证什么)
- [快速开始](#快速开始)
- [运行时架构](#运行时架构)
- [语言支持](#语言支持)
- [证据边界](#证据边界)
- [如何阅读报告](#如何阅读报告)
- [可复现实验](#可复现实验)
- [真实世界评测](#真实世界评测)
- [上游提交](#上游提交)
- [已知限制](#已知限制)
- [安全边界](#安全边界)
- [文档与开发](#文档与开发)
- [项目状态](#项目状态)

---

## SmartBench 正在验证什么

SmartBench 探索的是代码诊断中的一种职责划分：

- **语言前端和确定性分析器**拥有源码事实的解释权；
- **LLM 可以把**项目特有约定或风险作为 hypothesis 提出；
- **resolver 和 validator** 决定 hypothesis 能否重新绑定到真实
  operation、类型与控制流；
- **证据不足的结论**保留为 `unknown` 或 `abstained`，不能升级为 finding。

当某种项目约定过于局部、不值得写成一条语言级规则，而直接允许模型宣称
Bug 又缺乏可信边界时，这种分工才有价值。

| 路径 | 当前输出 | 不能据此证明什么 |
| --- | --- | --- |
| 确定性 `unified` 分析 | 规则 finding、capability、源码角色、图事实、JSON 和 SARIF | 干净结果不代表代码没有 Bug |
| 配置模型后的 `quick` | 项目 hypothesis、受 evidence gate 约束的审查、明确拒绝和 abstain 状态 | Agent 引用了真实 fact，不等于结论已经成立 |
| 公开 before/after corpus | 可复现地验证部分 analyzer 能区分 20 个已知修复 | 对未知 Bug 的 precision 或 recall |

目前更适合的使用者，是评估这套架构或进行受控、人工复核仓库审计的人；
它还不是可以直接放进 CI 作为质量门禁的工具。

## 快速开始

要求 Python 3.10+ 和 Git。

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[graph]"
```

### 确定性分析（不使用 LLM）

```bash
smartbench unified run \
  --project /path/to/repository \
  --output report.json \
  --sarif report.sarif
```

如需将结果作为 CI 质量门禁，使用 `--fail-on` 显式开启：

```bash
smartbench unified run --project . --fail-on warning
```

| 退出码 | 含义 |
| --- | --- |
| `0` | 没有达到或超过 `--fail-on` 阈值的 finding |
| `1` | 存在达到或超过阈值的 finding |
| `2` | 参数无效或内部错误 |

`--fail-on` 接受 `none`（默认）、`info`、`warning` 或 `error`。默认的
`none` 始终退出 `0`，所以必须加该参数才会改变行为。

如需生成适合 golden file 或 diff 的字节稳定 JSON，加
`--deterministic-output`，它会对运行时相关字段做归一化而不改变默认输出：

```bash
smartbench unified run --project . --output report.json --deterministic-output
```

### 受证据约束的 LLM 审查

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project /path/to/repository \
  --concern "检查正确性与资源生命周期风险" \
  --output agent-report.json
```

`quick` 路径运行完整融合链路：共享 AnalysisSession → 确定性规则 →
ProjectReader hypothesis → evidence resolver → Proposer → 源码验证器 →
Critique → Judge，全程由 evidence gate 约束。

### 提交上游 Issue 前先检查其他活跃分支

默认分支上的有效发现，可能已经在项目的 `dev`、`develop`、`next` 或
`release` 分支修复。提交 Issue 前运行：

```bash
smartbench check-branches \
  --input agent-report.json \
  --repo /path/to/repository
```

检查器同时支持 Quick 输出的 `path:line` 字符串和结构化位置；会比较
各分支上的清理模式、对报告函数做跨分支 diff，并运行 commit 历史
pickaxe 搜索（`git log -S`）。浅克隆缺少活跃分支时，只抓取该分支最新
提交，不切换分支、不修改工作区。若显示 `Already-fixed`，**不要重复
提交**，应先查看对应分支或待合并 PR。

机器可读输出（CI/脚本）：

```bash
smartbench check-branches --input results.json --repo /path/repo --json --output branch-check.json
```

### 可选依赖

本地向量检索是可选能力：

```bash
python -m pip install -e ".[graph,rag]"
```

不安装 `rag` 时仍会运行确定性图检索，只跳过本地向量索引。

## 运行时架构

主要 CLI 入口共享同一个 `AnalysisSession`。仓库发现、语言降低、
SemanticIR 构建和 SemanticLinker 只运行一次；确定性规则、检索、
ProjectReader 和多 Agent 都消费同一份结果。

```text
Repository
  -> ScanPlan -> language frontends -> SemanticIR -> SemanticLinker
  -> AnalysisSession
       |-> 确定性规则与声明式状态分析
       |-> 确定性 GraphRAG
       |-> ProjectReader hypothesis
             -> evidence resolver -> validator -> CFG lifecycle analyzer
       `-> EvidencePack { facts, hypotheses, source references }
             -> Proposer -> source verifier -> Critique -> Judge
  -> JSON / SARIF / benchmark report
```

| 命令 | 是否使用共享会话 | LLM 行为 |
| --- | --- | --- |
| `smartbench unified run` | 是 | 默认不使用 LLM，只运行确定性规则 |
| `smartbench quick` / 交互向导 | 是 | 可选 ProjectReader 与受证据约束的多 Agent 审查 |
| `smartbench benchmark run` | 是 | 不使用 LLM，运行固定快照与预期 |
| `smartbench eval-rag` | 是 | 不使用 LLM，评测会话 IR 上的检索 |
| `smartbench diagnose` | 不是代码语义入口 | 运行本地编译器、进程与工具探针 |

旧的 `CodeGraph -> SemanticIR.from_graph` 包装仍为库兼容和 fallback
保留，但主要 CLI 已不再用浅层兼容 IR 代替完整语言前端。

### 语义分析器

`smartbench/analysis/` 中的语言无关分析器只操作规范化 operation，
从不导入具体语言 parser：

| 分析器 | 证明什么 |
| --- | --- |
| 资源生命周期 | 语言无关的资源协议（获取 → 清理），带 CFG dominance 检查 |
| 状态机 | 声明式时序不变量（`require_guard_before_action`、`forbid_action_after_event`、`require_exit_after_event`）在有界 ICFG 上求值 |
| 控制流 / ICFG | 可达性、支配关系和有界跨过程路径 |
| 数据流（flow/） | 过程内 taint：从已知 source 到 SQL、命令、文件系统 sink |
| 类型证据 | 源码可见的 receiver 类型（Go：surface + `go/types` typeprobe；Python：注解 surface） |

## 语言支持

| 语言 | 当前层级 | 边界 |
| --- | --- | --- |
| Python | 深层语义 | CFG/ICFG、调用、状态规则、注解 surface 类型证据 |
| Go | 深层语义 | CFG/ICFG、状态/资源分析、surface + type-checker 类型证据（`go/types` via `tools/typeprobe`） |
| JavaScript / TypeScript | 部分语义 | 公共语句、调用与控制流；异步、异常和动态分派仍为 partial |
| Rust | 结构级 | tree-sitter 符号与图上下文，没有完整语义降低 |
| Java、Kotlin、C/C++、Ruby、Swift、C#、Zig | 识别/启发式 | 项目指纹和回退结构，不是编译器级分析 |

语言无关 analyzer 不导入具体语言 parser。新增语义前端必须提供稳定源码
位置、显式 capability、确定性 IR 序列化和至少一个 before/after
benchmark。

## 证据边界

`EvidencePack` 明确区分两类输入：

- `facts`：来自源码图或确定性分析器、具有稳定 `fact-*` ID 的事实；
- `hypotheses`：ProjectReader 解释和启发式规则候选，使用 `hypothesis-*` ID。

后续 Agent 可以读取 hypothesis 来决定"查什么"，但 evidence gate 不会把
它当作 fact。最终具体结论必须引用合法 fact ID；缺失、歧义、冲突或所有权
不明时保留为 `unknown` 或 `abstained`。

当前 debate gate 校验的是 fact ID 是否存在，不是"该事实是否在逻辑上
蕴含这条结论"。模型仍可能引用一个真实但不支持结论的 fact。因此，源码
位置核验、ProjectReader 的确定性验证和人工复核是彼此独立的要求；
typed conclusion-to-evidence relation 仍是待完成的契约。

ProjectReader 不能写入 SemanticIR。当前资源生命周期链路中，它只能选择
真实 CALL 并提出项目级清理协议，随后由程序：

1. 绑定真实 operation、cleanup fact 与 TypeEvidence；
2. 对零匹配或多匹配拒绝；
3. 校验 result binding、member path、可达性和类型选择；
4. 运行语言无关 CFG dominance 检查；
5. 产生 finding，或明确 abstain。

只有在确定性拒绝后才允许一次 bounded repair。repair 只能看到同一
inventory 和拒绝原因，看不到 analyzer 答案。

## 如何阅读报告

`unified` 报告包含 findings、capability assessment、source role、
repository zone、SemanticIR 统计、错误和有界 EvidencePack。`quick`
会把同一份确定性结果放在 `analysis_report`，与 Agent 审查一起输出。

| 状态 | 含义 |
| --- | --- |
| `full` | 相关前端满足该规则声明的全部要求 |
| `partial` | 运行了有文档边界的近似分析 |
| `unsupported` | 缺少必要语义，不能当成干净结果 |
| `unknown` | 规则没有声明足够语义要求，无法声称覆盖 |
| `abstained` | 证据缺失、歧义、冲突或可能存在 ownership transfer |

还需要注意：

- verifier 的 `verified`、`hallucinated` 描述的是源码位置和结构引用
  是否成立，不证明 Bug 结论正确；
- `consensus_reached` 当前表示 Proposer、Critique、Judge 三个阶段都
  返回了 schema 合法的输出；它是阶段完成标记，不是多个独立模型的统计
  一致率；
- Critique 或 Judge 失败时，Proposer 或 Judge 输出只会保存在
  `unreviewed_suggestions` 供审计，不会升级为 `final_suggestions`。
  控制台会明确显示 review 未完成，而不是把它报告成干净结果。

## 可复现实验

仓库包含 **20 个 before/after 案例（36 个最小源码快照）**——
`benchmarks/real/` 18 个、interprocedural 1 个、reasonix 1 个——来自
公开修复与 SmartBench 自发现 Bug。这些 fixture 保留声明式 analyzer
所需的代码，并不是历史仓库的完整 checkout：

| 项目 | 语言 | 公开修复 | 类别 |
| --- | --- | --- | --- |
| Requests | Python | [GHSA-j8r2-6x86-q33q](https://github.com/advisories/GHSA-j8r2-6x86-q33q) | 安全状态 guard |
| FastAPI | Python | [#5465](https://github.com/fastapi/fastapi/pull/5465) | 资源生命周期 |
| Prometheus | Go | [#1070](https://github.com/prometheus/prometheus/pull/1070) | 资源生命周期 |
| Kubernetes | Go | [#29495](https://github.com/kubernetes/kubernetes/pull/29495) | 资源生命周期 |
| Gin | Go | [#4422](https://github.com/gin-gonic/gin/pull/4422) | 资源生命周期 |
| Terraform | Go | [#38585](https://github.com/hashicorp/terraform/pull/38585) | 资源生命周期 |
| Sniproxy | Go | [PR #203](https://github.com/mosajjal/sniproxy/pull/203)（SmartBench） | 配置校验 |
| Qscan | Go | [Issue #22](https://github.com/qi4L/qscan/issues/22)（SmartBench） | 资源生命周期 |
| Stunner | Go | [Issue #89](https://github.com/firefart/stunner/issues/89)（SmartBench） | 资源生命周期 |

另有 9 个合成常见模式 fixture（HTTP body 关闭、SQL rows 关闭、文件
复制关闭、缓冲写入 flush、ticker Stop、mutex Unlock、context cancel、
websocket 关闭、requests session 关闭），用于固定状态规则引擎对常见
缺陷形态的识别能力。

```bash
smartbench benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

当前预期是 **36/36 快照检查通过**：声明的缺陷快照产生对应规则，修复
快照不产生。它证明 SmartBench 能表达这些已知缺陷，不测量未知 Bug
recall。

独立 blind ProjectReader 实验会从模型 inventory 中排除历史目标文件。
一次已记录的 DeepSeek A/B 在两个具有合法 reference 的 Go 协议上完成
6/6 trials；另外两个案例因 reference 不成立保持 unsupported。完整边界
见 [实验说明](benchmarks/experiments/project_reader_blind/README.md)。

## 真实世界评测

SmartBench 对 12 个 Python/Go 开源仓库进行了评测，结合确定性规则与
LLM 证据约束多 Agent 审查。共 410 条 finding，全部人工验证，随后对照
SDK 类型定义和项目文档进行交叉核查。

| 仓库 | 语言 | Stars | 确定性规则 | LLM Agent |
| --- | --- | --- | --- | --- |
| Flask | Python | 67k | 54 findings, 0 real | — |
| httpx | Python | 13k | 32 findings, 0 real | — |
| Bottle | Python | 8k | 21 findings, 0 real | 2 suggestions, 0 real |
| Litestar | Python | 5k | 269 findings, 0 real | — |
| resty | Go | 10k | 9 findings, 0 real | 3 suggestions, 0 real |
| Robyn | Python | 5k | — | 4 suggestions, 1 valid (test hygiene) |
| Reflex | Python | 20k | — | 1 suggestion, 0 real |
| PocketBase | Go | 43k | — | 3 suggestions, 0 real |
| Reasonix | Go | 80k+ | — | 1 suggestion, 0 real |
| Templ | Go | 8k | — | review failed (API timeout) |

对照 SDK 类型定义和项目文档后，**最初提交的 4 条中有 3 条被判定为误报**：

| 初始发现 | 仓库 | 纠正 |
| --- | --- | --- |
| 资源文件句柄未关闭 | Reasonix | SDK 类型为 `io.Reader` 非 `io.ReadCloser`；PR #7377 为 no-op 已关闭 |
| `panic()` 致备份恢复崩溃 | PocketBase | 函数注释明确说明为 fail-stop 设计；非 bug |
| 文件系统句柄泄漏 | PocketBase | `NewFilesystem().Close()` 直接返回 nil；无句柄打开 |

**1 条确认的有效观察**（测试卫生，非生产 bug）：

| 观察 | 仓库 | 上游 |
| --- | --- | --- |
| SSE 测试文件 16 处 `stream=True` 均未显式 `response.close()` | Robyn | [Issue #1432](https://github.com/sparckles/Robyn/issues/1432) |

所有上游线索均已更新为纠正后的评估。

关键观察：

- 确定性规则在真实项目上零误报 — 规则偏保守，不会在干净代码上瞎报；
- LLM 证据约束路径能提出看似合理的资源生命周期模式，但对照 SDK 类型
  和项目文档后，多数 grounding 不成立；
- Abstention 和 evidence gate 机制正确拒绝了证据不足的结论；
- **最大短板是 verifier 无法解析跨依赖的类型定义**——加强跨包类型解析
  是最高杠杆的改进方向。

### 类型检查器修复后的复测

为关闭上述缺口，实现了类型检查器适配层（`tools/typeprobe` +
`GoTypeCheckerProvider`），随后用同一批评测仓库重新评测。Go 仓库现在
通过 `go/packages` + `go/types` 产出 `TYPE_CHECKER` 类型证据：

| 仓库 | 类型检查器证据 | Closer 类型 | LLM 建议 | 验证通过 |
| --- | ---: | ---: | ---: | ---: |
| Flask | — | — | 0 | 0 |
| httpx | — | — | 1 | 0 |
| Bottle | — | — | 2 | 1 |
| Litestar | — | — | 1 | 1 |
| resty | 1,294 | 168 | 2 | 2 |
| Robyn | — | — | 2 | 1 |
| Reflex | — | — | 4 | 4 |
| PocketBase | 8,747 | 403 | 0 | 0 |
| Templ | 4,173 | 278 | 2 | 2 |
| Reasonix | 26,436 | 1,604 | 2 | 2 |
| **总计** | **40,650** | **2,453** | **16** | **13 (81%)** |

修复前 LLM 路径产出 18 条建议、4 条确认真实（22%）；修复后 16 条建议中
13 条通过验证（81%），0 条被拒。之前的两条误报消失：

- **Reasonix** 不再建议关闭 `resp.File`——解析类型为 `io.Reader`（无
  Close 方法），CrossChecker 的 `resource_type` 声明验证会拒绝此类结论；
- **PocketBase** 不再产出无法验证的资源生命周期建议。

**独立复核（codex + 人工）**：resty 的两条发现随后被复核，均判定为
**误报**——`multipart.Writer` 不持有 OS 资源（失败路径上 pipe writer
已关闭），而 `bodyBuf` 有全局兜底（`Request.Execute` 无条件调用
`backToBufPool(r.bodyBuf)`），缺失的局部 `releaseBuffer` 只是不一致，
不是泄漏。这确认了：内部验证通过率（81%）衡量的是声明 grounding，
**不是**真实 bug 精度；每条候选在提交前仍需跨依赖复核。

### 经过运行时验证的真实 Bug

以下发现通过了完整验证链——静态审计 → 运行时复现 → 本地修复 →
对照实验（修复前失败、修复后通过）——并已提交上游：

| 仓库 | 发现 | 验证证据 |
| --- | --- | --- |
| qi4L/qscan | `CheckSID` 所有错误路径泄漏 `*sql.DB`（缺 `defer db.Close()`） | goroutine 每次失败 +5 → 修复后 0 |
| firefart/stunner | `testPassword` 所有路径泄漏 TURN 连接 | 静默 mock server：修复后服务端 Read 才收到 EOF |
| mosajjal/sniproxy | `refresh_interval: 0` 导致进程崩溃：`time.NewTicker` 在 ACL 刷新 goroutine 中 panic（cidr/domain/geoip） | 运行时复现 panic → 修复对照 → 无 panic，测试通过 |

## 上游提交

经运行时验证（本地修复 → 复现 → 对照 → 重测）确认的发现已提交上游，
署名 SmartBench。cron job 每日监控这些线程并报告状态变化。

| Issue | 仓库 | 状态 | 发现 | 验证 |
| --- | --- | --- | --- | --- |
| [#22](https://github.com/qi4L/qscan/issues/22) | qi4L/qscan | OPEN | `CheckSID` 所有错误路径泄漏 `*sql.DB`（缺 `defer db.Close()`） | goroutine 每次失败 +5 → 修复后 0 |
| [#89](https://github.com/firefart/stunner/issues/89) | firefart/stunner | CLOSED | `testPassword` 所有路径泄漏 TURN 连接（爆破累积 socket） | 修复已在 `dev` 分支确认 — 发现正确，上游已自行修复 |
| [#1432](https://github.com/sparckles/Robyn/issues/1432) | sparckles/Robyn | OPEN | SSE 测试 `stream=True` 未关闭响应（测试卫生） | 确认真实，低严重度 |
| [#203](https://github.com/mosajjal/sniproxy/pull/203) | mosajjal/sniproxy | PR OPEN | `refresh_interval: 0` 导致进程崩溃：`time.NewTicker` 在 ACL 刷新 goroutine 中 panic（cidr/domain/geoip） | 运行时复现 panic → 修复对照 → 无 panic，测试通过 |

当 issue 被采纳关闭（或 PR 被合并）时，更新此表并将对应仓库加入成果清单。

## 已知限制

- 大多数内置规则仍是源码启发式；声明式状态规则和资源生命周期 analyzer
  是目前最强的语义案例。
- 异常流、异步调度、动态分派、别名分析、goroutine happens-before 和
  跨函数 channel alias 尚不完整。
- ProjectReader 生命周期 analyzer 当前只证明规范化的 defer-style cleanup。
- Python 前端会扁平化 `with`-context 清理操作，因此当前无法在 IR 中区分
  context-manager guard 与裸 acquire 调用。
- 大型仓库的延迟和内存还没有公开、稳定的预算。
- 本地向量/TF-IDF 缓存还不能跨所有可选依赖变化稳定复用；如果缓存是在
  安装 scikit-learn 时生成、之后又在缺少它的环境中打开，需要删除目标
  仓库的 `.smartbench/` 缓存后重建。
- `quick` 发送的仓库内容对所配置的远程模型供应商可见。
- 干净报告可能表示"没有受支持的 finding"，不等于"仓库没有 Bug"。

## 安全边界

- 源码路径被限制在仓库根目录内，外部 symlink 和 `../` 越界会被忽略。
- 外部命令不通过 shell，并带有时间和输出上限。
- 可选 `quick --sandbox` 只在临时副本中应用 patch，但测试仍拥有当前
  用户的系统权限。
- 除非允许发送给远程 provider，否则不要分析包含秘密或受限源码的仓库。
- 未经多次验证和人工决策，不应把 SmartBench finding 提交给上游项目。

## 文档与开发

- [架构文档](docs/ARCHITECTURE.md)
- [使用指南](docs/USAGE_GUIDE.md)
- [演示时间线](docs/DEMO_3_MINUTES_CN.md)
- [证据闭环说明](docs/EVIDENCE_LOOP_ARTICLE_CN.md)
- [类型证据教训：评测误报 → 验证层修复](docs/TYPE_EVIDENCE_LESSON_CN.md)
- [使用体验改进清单](docs/usage-experience-improvements.md)
- [历史 benchmark](benchmarks/real/README.md)
- [Blind ProjectReader 实验](benchmarks/experiments/project_reader_blind/README.md)

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI 运行 Python 3.10-3.12 测试、parser adapter 检查、36 快照 benchmark、
ProjectReader 边界实验和干净 wheel CLI 冒烟。

## 项目状态

SmartBench 适合受控仓库实验、架构研究和项目展示。它已经在真实项目中
产出经运行时验证的 Bug（qscan #22、stunner #89、sniproxy #203），并
发布了 20 案例 benchmark corpus 以及资源生命周期、状态机、数据流等
语言无关语义分析器。

下一步更有价值的是效果度量，而不是继续堆表层功能：

1. 在扩展后的 corpus 上公开 precision、recall、abstention rate、
   trial stability、延迟和成本；
2. 增加另一类 Agent-derived protocol 和另一个语义语言案例；
3. 让 Python 前端产出 `with`-context 清理操作；
4. 在不削弱 IR 边界的前提下深化异常、异步、类型、别名和并发语义。

## 许可证

[MIT](LICENSE)
