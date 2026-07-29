# SmartBench 三分钟演示

这份演示只展示已经可复现的能力。离线命令使用脚本化 Agent，因此不把它冒充为模型效果；真实 LLM 结果单独说明。

## 0:00–0:30：问题与边界

SmartBench 不让 LLM 直接判定 Bug。Agent 只提出项目语义假设，确定性系统负责绑定证据、拒绝歧义，并用 CFG 产生最终 witness。

```text
Agent hypothesis
  → unique-match evidence resolver
  → validator
  → bounded repair（失败时）
  → CFG before/after witness
```

## 0:30–1:30：现场运行证据闭环

```bash
python -m smartbench.experiments.evidence_loop_demo \
  --output /tmp/smartbench-evidence-loop-demo.json
```

脚本化 Agent 第一次故意把 `Close` 写成 `Release`。重点展示终端摘要中的五个字段：

1. `gate_rejected_initial_hypothesis: true`：错误假设没有进入分析器；
2. `repair_attempts: 1`：只允许一次有界修复；
3. `evidence_resolution: resolved`：系统而非 Agent 绑定 opaque ID；
4. `validator: supported`：replacement model 重新经过原 gate；
5. `cfg_witness: cfg_dominance_between_acquire_and_use`：最终结论来自控制流证据。

两个案例都应显示 `before=1 / after=0 / negative=0`。完整报告保存在参数指定的位置。

## 1:30–2:10：展示第二类缺陷与第二语言

```bash
smartbench benchmark run \
  --manifest benchmarks/real/requests_proxy_authorization_guard/manifest.yaml \
  --output /tmp/requests-security.json
```

这是 Requests 的 `GHSA-j8r2-6x86-q33q`：漏洞版本在写入代理认证头之前缺少 HTTPS tunnel guard，修复版本增加 guard。它走 Python SemanticIR 和通用 `call → guard → assign` 状态不变量，预期结果是 `before=1 / after=0`。

这证明了第二语言和第二 Bug 类别的 IR/CFG 承载能力，但不代表 Agent 已能自动发明任意安全规则。

## 2:10–2:40：真实 LLM A/B

2026-07-29 的 DeepSeek blind 实验中，模型看不到历史目标文件，repair 被关闭：

```text
旧版：3/6 trials 通过，主要失败为 opaque fact ID 复制不完整
新版：6/6 trials 通过，6 个候选全部唯一解析，0 rejection
负样本：0 finding
```

准确表述是“两个 reference-backed Go 资源案例上的 6 次 trial”，不能称为通用准确率。

## 2:40–3:00：总结

SmartBench 当前最有价值的不是规则数量，而是可审计的信任边界：模型负责提出可能性，确定性架构决定证据是否唯一、结论是否成立以及何时 abstain。下一步是扩大外部 blind corpus、Bug 类别和语言，而不是为每个项目打补丁。
