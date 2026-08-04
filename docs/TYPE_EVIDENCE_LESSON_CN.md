# 当"类型"在依赖边界上消失：一次评测误报如何暴露验证层的盲区

## 起因：一次真实世界评测

2026-08-04，SmartBench 对 12 个 Python/Go 开源仓库做了组合评测：确定性规则 +
LLM 证据约束多 Agent 审查（DeepSeek）。410 条 finding 全部人工验证，其中 LLM
路径产出了 4 条"资源生命周期"候选，我们对上游提交了 1 个 PR 和 3 个 Issue。

随后用独立工具对 4 条候选逐一复核，结果 3 条不成立：

| 候选 | 结论 | 根因 |
| --- | --- | --- |
| Reasonix `resp.File` 未关闭 | 误报 | SDK 类型是 `io.Reader`，不是 `io.ReadCloser`；补丁是 no-op |
| PocketBase `panic()` 致崩溃 | 误报 | 函数注释明确写明 panic 是 fail-stop 设计 |
| PocketBase 测试句柄泄漏 | 误报 | `Close()` 实现直接 `return nil`，没有打开句柄 |
| Robyn SSE 测试连接泄漏 | 成立 | 测试卫生问题，非运行时 bug |

PR 已关闭，Issue 已纠正。但真正值得追问的是：**SmartBench 为什么会产生这三条
错误结论，而且它们还通过了内部的三轮辩论？**

## 根因一：location verification 只验"位置存在"，不验"类型成立"

三类误报共享同一条失效链路：

```
Agent 提出：resp.File 需要 close
    ↓
CrossChecker 检查：internal/bot/feishu/inbound.go:222 存在 ✅
    ↓
结论：claim 通过 → 进入 final_suggestions
```

evidence gate 校验的是"文件路径和行号是否真实存在"，而不是"这个符号的真实类型
是什么"。Reasonix 场景里，`resp.File` 的类型定义在 larksuite SDK 的
`GetMessageResourceResp` 结构里（`File io.Reader`），不在被分析仓库的源码中。
surface 类型提供器（`go.surface`）明确不模拟 `go/types`，只看源码声明，所以它
对 `resp` 的类型一无所知——但 Agent 的模式匹配（"ReadAll 后没 Close → 泄漏"）
照样提出了结论，而位置验证又放行了它。

PocketBase #7789 是另一种失效：代码位置真实存在、`panic()` 也确实在，但函数
注释写明这是故意的 fail-stop 设计。位置验证读不到语义意图。

## 修复设计：复用现有框架，只动适配层

盘点 SmartBench 已有资产后发现，**契约、索引、消费端全部就位，只缺一个 provider**：

- `TypeEvidenceSource` 枚举早已预留 `TYPE_CHECKER`（rank 3）和 `LIBRARY_CONTRACT`，
  但没有任何 provider 产出它们；
- `TypeEvidenceIndex.unique_type()` 已经能按 operation+role 查询唯一类型；
- `resource_lifecycle.py` 已经在消费 RECEIVER 类型证据，没有就 abstain。

因此修复是"填空"而不是"造新"：

```
tools/typeprobe（Go helper，go/packages + go/types）
    ↓ 通过 subprocess 批量解析 file:line:symbol 的真实类型
smartbench/frontends/go_type_checker.py（GoTypeCheckerProvider）
    ↓ 产出 TypeEvidence(source=TYPE_CHECKER)，挂到 CALL 的 receiver / result
core/adapters/go.py（挂载，+3 行）
    ↓
resource_lifecycle 现有逻辑自动受益（rank 3 > surface rank 2）
```

语言相关的全部差异被封装在 Go 前端适配层：SemanticIR、证据契约、分析器、
CrossChecker 的接口零改动。没有 Go 工具链时 provider 记录 error 并产出空结果，
走已有的 abstain 语义，绝不发明类型。

typeprobe 通过一次 `go/packages` 加载服务整批查询（stdin/stdout JSON，500
条一批），实测 Reasonix 全量 42146 条查询、26436 条证据、1604 条 Closer 类型。

## 修复验证：用当初的误报做回归

闭环验证用最小 fixture 精确复现 Reasonix 场景——一个本地 `sdk` 包声明
`Resp{ File io.Reader }`，`main` 包调用 `sdk.GetResource()` 后读取 `resp.File`：

| 提供器 | `resp` 的类型 | 结论 |
| --- | --- | --- |
| surface（旧） | 空白（错误解析成 `[]byte/error`） | 无法判断 |
| type-checker（新） | `*sdk.Resp`（`has_close_method=False`） | 资源声明被反驳 |

CrossChecker 新增 `resource_type` claim 类型：Agent 声称某符号需要 Close 时，
verifier 查类型证据——类型有 Close 方法则 verified，没有则 hallucinated，无证据
则 abstain。对 `resp.File` 还做了前缀回退（`resp.File` 无直接证据时回退查
`resp`），使当初那条误报在验证层被机械拒绝，而不是靠人工拦截。

回归测试固化了这个场景（`tests/test_go_type_checker.py` 的
`test_sdk_field_type_resolution_prevents_false_positive` 和
`tests/test_verifier_type_claims.py`）。

## 仍然诚实的边界

- 类型证据只能反驳"类型不成立"的 claim。PocketBase #7789 那类"行为是有意设计"
  的误报，需要的是文档/意图感知（函数注释），类型解析解决不了；
- `io.Reader` 在 `go/types` 里没有 `Close()`，但某些 SDK 的"资源"语义是约定而
  非接口（如 `exec.Cmd` 用 `Wait()` 而不是 `Close()`）。协议层识别仍靠
  resource-lifecycle analyzer，类型证据只是它的一个输入；
- 大仓库仍有 `max_files` 预算（默认 500 文件），超出部分不在分析范围内；
- 修复有效性的定量结论要等重跑同一批评测，用 precision 变化说话，而不是"修好了"。

## 结论

这次误报不是 Agent 太笨，而是**验证器的信任边界太宽**：它验证了"位置存在"，
却假装验证了"结论成立"。type-checker 适配层把边界收窄到类型事实——位置由
location verifier 负责，类型由 go/types 负责，语义意图留给文档与人工。每一层
只承诺它能证明的东西，这正是 SmartBench 从一开始就想坚持的证据边界原则。
