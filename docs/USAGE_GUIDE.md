# SmartBench 使用指南

[返回 README](../README_CN.md)

本文档对应当前 `smartbench` CLI。`legacy/` 中的旧 Raft 压测命令不属于当前命令行入口。

## 安装

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

可选依赖：

```bash
# 开发、测试和五种 tree-sitter 语言适配器
python -m pip install -e ".[dev,graph]"

# 本地向量检索
python -m pip install -e ".[rag]"
```

## 命令

### `smartbench`

启动四步交互式向导：选择本地目录或 Git URL、配置模型、扫描项目、输入诊断目标。向导输入的 API Key 仅保存在当前进程内存中。

### `smartbench quick`

使用环境变量中的模型凭证，减少交互步骤：

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
应用或基线本身失败时都不会显示“验证通过”。临时副本只能避免修改原工作区，并不
隔离测试进程；测试代码仍拥有当前用户权限。

未配置模型时，快速模式仍会显示仓库指纹和代码图统计，但不会执行 LLM 审查。

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

`--output <path>` 将 `DebateResult` 或诊断结果原子写入 JSON。输出路径的父目录必须已存在；序列化或写入失败时命令返回非零状态码。启用 `--sandbox` 后，每条建议的 `__sandbox_verification` 也会保存在报告中。报告可能包含代码片段和诊断命令输出，提交或分享前应检查其中是否存在敏感信息。

## 如何解读证据状态

- `verified`：引用的文件与行号存在，且可选符号或调用关系检查通过。
- `partial`：找到了可能对应的文件，但路径经过模糊修正，或只验证了部分声明。
- `hallucinated`：文件不存在、行号非法，或声明的调用关系与当前结构图冲突。
- `unverifiable`：诊断没有提供足够具体的可核查证据。

这些状态只描述“引用是否有证据”，不等价于缺陷已被证明。最终结论仍应通过测试、编译器、Linter、Profiler 或人工审查确认。

## 本地产物

启用 RAG 时，SmartBench 会在目标仓库创建 `.smartbench/vector_store/`。常规流程不会编辑源文件，但建议将 `.smartbench/` 加入目标仓库的 `.gitignore`。扫描、检索和证据验证只读取项目根目录内的常规文件，指向仓库外部的符号链接与 `../` 越界路径会被忽略。

## 开发者验证

```bash
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```
