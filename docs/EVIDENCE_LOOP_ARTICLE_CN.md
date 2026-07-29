# 让 LLM 提出假设，让程序决定证据：SmartBench 的双架构诊断闭环

## 为什么不是“让模型读完整仓库然后找 Bug”

代码诊断同时需要两种相反的能力：理解项目约定需要开放语义推理，而证明控制流、调用位置和证据来源需要稳定、可重复的计算。只用静态规则容易陷入为语言和项目不断打补丁；只用 LLM 又会遇到引用漂移、结论不可复现和误报无法审计。

SmartBench 将两者拆成不同信任级别：ProjectReader 只能输出 `ProjectModel` 假设，不能写入 SemanticIR，也不能直接生成最终 Finding；语言前端、EvidencePack、resolver、validator 和 CFG analyzer 才拥有事实权。

## 为什么必须先统一 AnalysisSession

早期实现里，`unified` 运行完整 SemanticIR 与规则，`quick` 却从 CodeGraph 包装浅层 IR，ProjectReader 闭环又只存在于实验 runner。算法组件虽然都在，但没有共享同一次仓库分析；因此“Agent 提出假设、程序验证证据”只在局部成立。

现在主要入口先建立一个 `AnalysisSession`：ScanPlan、语言前端、SemanticIR 和 SemanticLinker 只运行一次。`unified`、`quick`、benchmark 和 RAG evaluator 都消费这份会话；`quick` 再在其上启用 ProjectReader 与多 Agent。旧 `SemanticIR.from_graph` 只保留为库兼容和 fallback，不再是主要 CLI 的事实来源。

## 一条候选如何变成 Finding

```text
Repository
  → ScanPlan → language frontend → SemanticIR → SemanticLinker
  → AnalysisSession
  → bounded reference inventory
  → ProjectReader semantic hypothesis
  → deterministic evidence resolver
  → project-model validator
  → resource/state analyzer
  → before/after + negative witness
```

Agent 选择 operation、result index、cleanup method、member path，以及可选的 receiver/canonical type 假设。它不再负责复制 `fact-*` 和 `type-*` ID。resolver 根据结构关系寻找 primary result-call、同 scope 且 acquire 后可达的 cleanup registration，以及独立的 TypeEvidence。

ProjectReader 输出进入 EvidencePack 时仍被标为 `hypothesis-*`，不能通过 fact-ID gate。只有 resolver/validator/CFG analyzer 产生的 source-backed `fact-*` 才能支撑最终建议。启发式规则候选也使用 hypothesis 通道，避免“程序输出”被误写成“语义事实”。

解析规则是保守的：恰好一个匹配才是 `resolved`，零个匹配是 `unresolved`，多个匹配是 `ambiguous`。后二者都 abstain，不会进入 validator。旧客户端提交的 ID 会进入 `agent_cited_evidence` 审计区，但 analyzer 只消费 `deterministically_resolved_evidence`。

validator 仍然检查 symbol、binding、member path、CFG reachability、receiver type 和 canonical method。resolver 没有放松原 gate，只是把不适合交给语言模型的 opaque ID 搬回了确定性系统。

真实在线合流测试还暴露了另一种机械失败：模型选中了正确的真实 CALL operation，却把源码接收者拼写规范化成类型符号。resolver 现在以已选中的真实 operation target 为权威，并在 `selector_normalizations` 同时记录 Agent 值和 resolved 值；cleanup、binding、reachability、type 与 CFG 校验均保持不变。

## 为什么还保留 bounded repair

resolver 能修复“语义正确但 ID 抄错”的机械失败，却不应修复错误语义。例如 Agent 把 cleanup method 识别成 `Release`，resolver 会返回零匹配。此时 repair 只能看到同一份 blind inventory、上一次结构化输出和拒绝原因；它看不到历史 target、before/after analyzer 结果，也不能改变 validator。replacement model 必须从头通过同一套 gate。

因此 repair 是受约束的自我纠错，不是让模型反复猜到 benchmark 通过。报告同时保留 initial rejection、repair attempts、recovered trials 和最终决策。

## A/B：resolver 是否真的解决了问题

在 2026-07-29 的真实 DeepSeek blind trials 中，两个案例有合法、经过哈希固定且排除目标文件的 reference：Prometheus 和 Kubernetes。Gin 与 Terraform 因没有合法 reference 保持 unsupported，模型不会被调用。

无 repair 对照结果：

| 配置 | 通过 | 初始拒绝 | repair | 负样本 finding |
| --- | ---: | ---: | ---: | ---: |
| 旧版，由模型复制 ID | 3/6 | 3 | 0 | 0 |
| 新版，由 resolver 绑定 ID | 6/6 | 0 | 0 | 0 |

新版的 6 个候选全部复现历史 before finding，after 为零，独立负样本为零。这个结果说明 resolver 修复了已观察到的证据引用不稳定，但样本仍只有两种 Go 资源协议，绝不能写成“Bug 检测准确率 100%”。

## 第二语言与第二类缺陷

为了验证架构没有锁死在 Go 的 `defer Close`，SmartBench 加入 Requests 的公开安全修复 `GHSA-j8r2-6x86-q33q`。漏洞是给 HTTPS tunnel request 写入 `Proxy-Authorization` 前缺少 scheme guard；修复提交增加 `not scheme.startswith("https")`。

同一 StateMachineAnalyzer 在 Python SemanticIR 上消费声明式 `call → guard → assign` 不变量，得到 `before=1 / after=0`。这证明语言前端只需降级到公共 operation/CFG 契约，核心 analyzer 不必导入 Python parser。它仍是已知 before/after 表达能力证据，而不是未知安全漏洞 recall 证明。

## 当前边界和下一步

当前最强的证据是：双架构已经真的串联，并且确定性反馈能降低模型随机失败；最弱的部分仍是 corpus 规模和协议类型。下一阶段应扩大到 10–20 个外部 before/after、更多自然负样本、第二种 Agent-discovered protocol，并统计 precision、recall、abstention、trial stability 和成本。

SmartBench 的目标不是消灭不确定性，而是让每一份不确定性停在正确的边界，让每一个被接受的结论都能回答三个问题：证据从哪里来、为什么唯一、在哪些条件下系统选择沉默。
