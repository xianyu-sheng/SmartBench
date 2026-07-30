# SmartBench 使用指南

[返回 README](../README_CN.md)

本文档对应当前 `smartbench` CLI。`legacy/` 中的旧 Raft 压测命令不属于当前命令行入口。

## 安装

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[graph]"
smartbench --version
```

可选依赖：

```bash
# 开发与测试
python -m pip install -e ".[dev,graph]"

# 本地向量检索
python -m pip install -e ".[rag]"
```

## 命令

### `smartbench`

启动四步交互式向导：选择本地目录或 Git URL、配置模型、构建统一分析会话、输入诊断目标。`Concern (analyze the project for issues):` 表示正在等待输入；直接按 Enter 会采用括号内的默认值。向导输入的 API Key 仅保存在当前进程内存中。

### `smartbench quick`

使用环境变量中的模型凭证，减少交互步骤。`quick` 与 `unified` 共享同一个 `AnalysisSession`，不会再从结构图重建一份浅层 IR：

```bash
smartbench quick \
  --project ./my-project \
  --concern "检查并发和错误处理" \
  --output report.json
```

也可以使用主命令快捷参数：

```bash
smartbench --quick --project ./my-project --concern "检查安全风险"
```

如需验证 Judge 给出的机器可应用补丁，可以仅对可信仓库显式开启：

```bash
smartbench quick --project ./my-project --sandbox
```

该选项先在临时副本中运行基线测试，再应用 unified diff 并复测。没有补丁、补丁无法
应用或基线本身失败时都不会显示“验证通过”。补丁只能修改建议中声明的目标文件，
测试子进程会移除凭证型环境变量；但临时副本只能避免修改原工作区，并不隔离测试
进程，测试代码仍拥有当前用户权限，也可能访问网络或用户文件。

SmartBench 对本地诊断、Git 元数据读取和补丁验证子进程设置超时与输出上限，
避免异常工具无限占用内存；这仍不等价于操作系统沙箱。

未配置模型时，快速模式仍会返回完整 SemanticIR 与确定性规则报告，只跳过 ProjectReader 和多 Agent 审查。

### `smartbench check`

根据当前目录识别出的主要语言，显示本机可用的诊断探针：

```bash
cd ./my-project
smartbench check
```

### `smartbench diagnose`

跳过 LLM 辩论，运行与语言和问题类别匹配的本地诊断探针：

```bash
smartbench diagnose --project ./my-project
smartbench diagnose --project ./my-project --perf --output diagnostics.json
smartbench diagnose --project ./my-project --perf --system-probes
```

`--symptoms` 会作为症状描述传入诊断阶段。默认只运行项目级探针；主机级的
`ps`、`vmstat` 和 `dmesg` 必须通过 `--system-probes` 显式开启，其输出可能包含
主机进程或内核信息。诊断探针可能执行本机命令，因此只应对信任的目标路径运行。

### `smartbench unified`

统一诊断入口将仓库前端、版本化 SemanticIR、规则引擎和 JSON/SARIF 输出连接起来：

```bash
smartbench unified rules
smartbench unified languages
smartbench unified run --project ./my-project
smartbench unified run \
  --project ./my-project \
  --language python \
  --rule null_dereference \
  --sarif report.sarif \
  --output report.json
```

Python 和 Go 会降低到共同的语义操作模型，支持保守的跨函数调用/返回、数据流、有限
ICFG 和声明式状态规则。JavaScript/TypeScript 也提供公共 statement/call/control-flow
operation，但 async、exception、动态分派和类型检查仍为 partial。Java 与 Rust 当前主要
提供结构图兼容能力；未解析语义会保留为 unknown/partial。

`unified` 默认不调用 LLM。`quick` 会在同一会话上额外运行 ProjectReader 与 Agent 审查；
ProjectReader 接受的 CFG finding 会写入 `analysis_report`，而不是另建一份不可比较的结果。

声明式状态规则位于 `smartbench.state-rules/v1` schema 中，可以通过重复的
`--state-rules` 选项加载。`scope: interprocedural` 规则使用有界跨函数路径；无法证明
调用者守卫确实控制动作时会保持 unknown，而不是直接产生误报。

### `smartbench benchmark run`

基准运行器对声明的 before/after 仓库快照执行同一套统一引擎，并检查发现数量和规则 ID：

```bash
python -m smartbench.cli.main benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

它适合保存可复现的架构回归和真实 Bug 结果，不应被解读为跨语言通用准确率评测。当前
`benchmarks/real/manifest.yaml` 包含 Requests、FastAPI、Prometheus、Kubernetes、Gin 和
Terraform 六个公开 before/after 案例，共 12 个快照。

### `smartbench eval-rag`

使用带标注的查询集测量 `Hit@k`、`Precision@k`、MRR 和平均延迟：

```bash
smartbench eval-rag \
  --project ./my-project \
  --queries ./eval_queries.json \
  --output rag-report.json
```

加上 `--graph-only` 可只评测结构检索。查询文件是 JSON 数组，每项至少包含
`query` 和目录明确的 `expected_file`；同名但位于其他目录的文件不会被误算为命中。

## 模型凭证

快速模式当前支持以下凭证：

| 环境变量 | 供应商 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `OPENAI_API_KEY` | OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic（原生 Messages API） |
| `GLM_API_KEY` | 智谱 GLM |
| `DOUBAO_API_KEY` | 豆包 |
| `MOONSHOT_API_KEY` | Moonshot / Kimi |
| `DASHSCOPE_API_KEY` | 通义千问 |

不要把 Key 写进 Git、README、命令输出或诊断报告。可以使用 `SMARTBENCH_<PROVIDER>_MODEL` 覆盖快速模式的默认模型，例如 `SMARTBENCH_DEEPSEEK_MODEL` 或 `SMARTBENCH_ANTHROPIC_MODEL`。

## 输出报告

`--output <path>` 将诊断结果原子写入 JSON。`unified` 输出完整 session 报告；`quick` 在
`analysis_report` 中保存同一份确定性报告，并在外层保存 Agent 建议与 debate log。输出路径
的父目录必须已存在；序列化或写入失败时命令返回非零状态码。启用 `--sandbox` 后，每条
建议的 `__sandbox_verification` 也会保存在报告中。报告可能包含代码片段和诊断命令输出，
提交或分享前应检查其中是否存在敏感信息。

`analysis_report.project_reader.status` 可能为 `not_run`、`unsupported`、`unavailable`、
`abstained`、`supported_no_finding` 或 `findings`。网络/API 失败使用 `unavailable`，不会被
伪装成干净结果。

## 如何解读证据状态

- `verified`：引用的文件与行号存在，且可选符号或调用关系检查通过。
- `partial`：找到了可能对应的文件，但路径经过模糊修正，或只验证了部分声明。
- `hallucinated`：文件不存在、行号非法，或声明的调用关系与当前结构图冲突。
- `unverifiable`：诊断没有提供足够具体的可核查证据。

这些状态只描述“引用是否有证据”，不等价于缺陷已被证明。`consensus_reached` 表示
Proposer、Critique、Judge 三个阶段都返回 schema 合法的输出，不是独立模型的统计共识。
Critique 或 Judge 失败时，结构化 hypothesis 只保存在 `unreviewed_suggestions`，不会进入
`final_suggestions`。最终结论仍应通过测试、编译器、Linter、Profiler 或人工审查确认。

EvidencePack 中的 `facts` 才能被最终建议引用；`hypotheses` 保存 ProjectReader 解释和
启发式规则候选，只用于决定后续调查方向，不能通过 fact-ID gate。

## 本地产物

启用 RAG 时，SmartBench 会在目标仓库创建 `.smartbench/vector_store/`。常规流程不会编辑源文件，但建议将 `.smartbench/` 加入目标仓库的 `.gitignore`。扫描、检索和证据验证只读取项目根目录内的常规文件，指向仓库外部的符号链接与 `../` 越界路径会被忽略。

索引默认最多处理 2,000 个文件、单文件 2 MB、10,000 个分块；依赖和缓存目录会在遍历阶段直接剪枝。分块大小、重叠量与资源上限会在运行前校验，非法配置会立即失败而不是进入死循环。

SmartBench 会把仓库元数据、README、源码、日志、工具输出和前序模型输出标记为不可信数据，并转义内部数据分隔符，以降低仓库内容注入指令的风险。这不是模型级安全隔离；若仓库包含不能发送给所配置模型供应商的秘密，不应对其运行 LLM 诊断。

## 开发者验证

```bash
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI 还会把构建出的 wheel 安装到干净虚拟环境，并从源码目录外执行 CLI 冒烟测试。
