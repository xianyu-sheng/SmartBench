# SmartBench 使用体验改进清单（2026-08-06 实测）

来源：sniproxy 完整融合评测（确定性扫描 + ProjectReader + 多角色辩论 +
证据核查 + check-branches + 运行时验证）过程中发现的真实摩擦点。
按影响排序。

## 1. LLM provider 配置只能走环境变量，无法复用已有配置

**现象**：`smartbench quick` 的模型配置依赖 `ENV_PROVIDER_MAP`
（`DEEPSEEK_API_KEY` 等）。本次 DeepSeek 官方 402 后，只能用
`SMARTBENCH_DEEPSEEK_BASE_URL` + `SMARTBENCH_DEEPSEEK_MODEL` 环境变量
硬切到 heyroute；Hermes 的 `custom_providers` 配置无法被 SmartBench
直接读取。

**建议**：
- 支持 `smartbench quick --provider <name> --model <model>` 或
  `--api-config <json>`，并读取 `~/.hermes/config.yaml` 的
  `custom_providers`（或项目自己的 `smartbench.toml`）；
- 至少支持 `SMARTBENCH_LLM_BASE_URL` / `SMARTBENCH_LLM_MODEL` /
  `SMARTBENCH_LLM_API_KEY` 三个通用覆盖变量，避免依赖 deepseek 前缀。

## 2. provider 失败时报告不透明（402/401 没有结构化原因）

**现象**：sniproxy quick 在 DeepSeek 402 时输出
`status=unavailable, protocols=0, findings=0`，Proposer 显示
"解析失败（无输出）"，报告标注 `review=failed`，但最终 JSON 里
没有结构化的 provider 错误原因字段，无法区分"余额不足 / token 无效 /
模型不存在 / 网络错误"。

**建议**：`call_llm` 把最后一次失败原因（provider、HTTP 状态码、
错误正文摘要）写入报告的 `llm_status` / `provider_errors` 字段，
并在控制台用明确文案（如 `provider deepseek: HTTP 402 Insufficient
Balance`）。

## 3. quick 输出的 location 格式与 check-branches 不兼容

**现象**：quick 的 `final_suggestions[].location` 是组合字符串
`"pkg/acl/cidr.go:110-124, pkg/acl/cidr.go:164-174"`，而
`check-branches` 的 `_parse_location` 只解析单个 `path:line[-line]`，
导致把整串当文件名，报 `file not found`，去重检查全部落空。

**建议**：
- quick 输出结构化 `locations: [{file_path, line_start, line_end}]`；
- `check-branches` 的解析器支持逗号分隔的多 location，逐个检查。

## 4. unified 报告与 quick 报告 schema 不统一

**现象**：`unified run` 顶层是 `findings[]`，quick 顶层是
`final_suggestions[]`。`check-branches` 只认 `final_suggestions`，
对 unified 报告输出 "No suggestions found"，实际是"格式不识别"。

**建议**：`check-branches` 同时接受 `findings` 顶层；或提供
`smartbench normalize --from unified|quick` 转换子命令。

## 5. 辩论过程缺少中间产物可见性

**现象**：`quick` 控制台只展示最终 suggestion 卡片；Proposer /
Critique / Judge 的原始论据只在 verbose 时可见，报告 JSON 里虽有
`debate_log` 但结构未文档化。

**建议**：
- 在 README 中记录 `debate_log` 的结构；
- 提供 `--show-debate` 标志，输出每个角色的完整论据与证据引用。

## 6. 证据核查得分与最终建议的关联不清晰

**现象**：报告有 `review_status: "failed"` 但 `final_suggestions` 仍
非空（sniproxy 402 那次的 Quick 输出），读者无法判断这些建议是
"辩论通过"还是"降级保留"。

**建议**：每个 suggestion 增加 `verification_score` / `review_status`
字段；`review=failed` 时控制台显式警告"以下建议未经完整证据核查"。

## 7. 确定性扫描对超大仓库无保护

**现象**：>300 文件的仓库（brutus 311 文件）`quick` 跑了 20+ 分钟
未完成；无文件数上限提示。

**建议**：`quick` / `unified run` 在文件数超过阈值（如 300）时
打印警告并要求确认，或提供 `--max-files` 截断选项。

## 8. RAG 向量索引降级信息不足

**现象**：`PyTorch not available, skipping sentence-transformers`
后自动用确定性字符嵌入，但控制台未明确说明当前使用的是降级模式，
报告也未记录 embedder 模式。

**建议**：在 fingerprint / 报告里记录 `embedder: deterministic|ml`，
降级时提示"检索精度降低"。

## 9. 资源泄漏规则的接受-拒绝路径覆盖有限

**现象**：`resource_leak` 规则对 `os.Open` 后同一函数内有
`defer Close()` 的情况能正确拒绝（sniproxy `LoadCIDRCSV` 无误报），
但对 goroutine 内 panic 类（`time.NewTicker(0)`）没有提示——
此类问题只能靠多角色辩论发现。

**建议**：为"goroutine 内创建 ticker/连接而无 defer 清理"增加
独立规则，或把 `time.NewTicker` 纳入资源生命周期分析。

## 10. check-branches 的 evidence 字段在 JSON 模式下缺失已检查分支明细

**现象**：`--json` 输出有 `checked_branches`，但 rich 表格模式只显示
`Fixed on` 和 `Evidence` 两列，未显示具体分支名来源（origin/dev 等）。

**建议**：rich 表格增加"来源"列，与 JSON 的 `checked_branches` 对齐。

---

### 优先级建议

| 优先级 | 项 | 理由 |
|--------|----|------|
| P0 | 1, 2 | provider 切换与失败诊断是评测流程可用性的前提 |
| P1 | 3, 4 | location/schema 统一直接决定 check-branches 是否生效 |
| P1 | 6 | 证据得分与建议关联影响结果可信度 |
| P2 | 5, 7, 8, 9, 10 | 可观测性、保护机制与规则覆盖增强 |
