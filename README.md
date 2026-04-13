# SmartBench v0.3

**智能 Raft KV 性能优化分析工具** - 基于多 Agent 辩论引擎的分布式存储系统诊断平台

---

## 核心定位

SmartBench 是一款专注于 **Raft 分布式 KV 存储系统** 的性能分析与优化建议工具。它通过多模型辩论机制（Proposer/Critique/Judge）对压测数据、日志、源码进行深度分析，生成**具体可实施**的优化建议。

**重要原则**: SmartBench 仅分析和建议，**不修改**被测项目任何代码。

---

## 核心特性

### 1. 多 Agent 辩论引擎
创新的三角色辩论机制：
- **Proposer (方案提出者)**: 分析日志和源码，生成具体优化方案
- **Critique (交叉审查者)**: 审查方案的潜在风险和可行性
- **Judge (最终仲裁者)**: 综合意见，输出最终建议

### 2. 代码智能缓存
- 自动缓存源码文件，仅在文件变更时重新读取
- 智能提取关键代码片段供 AI 分析
- 大幅降低 API token 消耗

### 3. 性能回归分析
- 记录每次压测结果
- 对比优化前后 QPS、延迟变化
- 检测性能回归，生成趋势报告

### 4. 代码位置验证
- 验证 AI 建议的代码位置是否正确
- 提取原始代码上下文
- 分析修改的语法正确性

### 5. 多模型协作
支持多种 AI 模型并行分析：
- DeepSeek V3
- GLM-4.7
- Doubao-Seed-2.0-Code
- Doubao-Seed-2.0-Pro

---

## 快速开始

### 环境要求
- Python 3.10+
- Linux/macOS
- ZooKeeper (用于 Raft KV 集群)
- CMake 3.10+

### 安装

```bash
cd /home/xianyu-sheng/SmartBench
pip install -e .
```

### 配置 API Key

编辑 `config/default.yaml` 或设置环境变量：

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"
export GLM_API_KEY="your-glm-api-key"
export DOUBAO_API_KEY="your-doubao-api-key"
```

### 一键运行

```bash
# 使用启动脚本
cd /home/xianyu-sheng/SmartBench
./run.sh

# 或直接使用 CLI
python3 -m smartbench.cli run --target-qps 400 --rounds 1
```

---

## 命令详解

### 1. `run` - 完整分析流程

执行压测、辩论分析、生成建议的完整流程：

```bash
python3 -m smartbench.cli run [OPTIONS]

# 参数说明
--system, -s         目标系统类型 (默认: raft_kv)
--target-qps, -q     目标 QPS (默认: 400)
--rounds, -r         压测轮次 (默认: 1)
--analysis-rounds     分析轮次 (默认: 1)
--models, -m         指定模型 (逗号分隔)
--verbose, -v        显示详细日志
```

**示例**:

```bash
# 标准运行
python3 -m smartbench.cli run --target-qps 500

# 指定多模型
python3 -m smartbench.cli run --models deepseek,glm-4.7

# 详细模式
python3 -m smartbench.cli run --verbose
```

**输出示例**:

```
╭──────────────────────────────────╮
│ SmartBench v0.3 - Raft KV 专用版 │
╰──────────────────────────────────╯

目标系统: raft_kv
目标 QPS: 400.0
使用模型: deepseek, glm-4.7, doubao-seed-code

⠏ ✓ 完成！                                            100%

╭───────────────╮
│ ✅ 分析完成！ │
╰───────────────╯
       性能指标       
┏━━━━━━━━━━┳━━━━━━━━━┓
┃ 指标     ┃      值 ┃
┡━━━━━━━━━━╇━━━━━━━━━┩
│ 当前 QPS │   387.7 │
│ 目标 QPS │   400.0 │
│ 平均延迟 │ 2.40 ms │
│ P99 延迟 │ 7.66 ms │
│ 错误率   │   0.00% │
└──────────┴─────────┘

分析模型: 辩论引擎 (Proposer/Critique/Judge)

优化建议 (共 2 条):

1. 优化AE RPC并行 🟡
   ⭐⭐⭐⭐⭐ | 来源: judge
   📝 Leader串行同步发送AE RPC导致延迟累加...

2. 心跳与日志分离 🟢
   ⭐⭐⭐⭐ | 来源: judge
   📝 心跳与日志复制共用AE RPC通道...
```

### 2. `check` - 集群健康检查

检查 Raft KV 集群状态：

```bash
python3 -m smartbench.cli check [OPTIONS]

# 参数说明
--system, -s         目标系统类型 (默认: raft_kv)
--project-path, -p   项目路径
```

**示例**:

```bash
python3 -m smartbench.cli check
python3 -m smartbench.cli check --project-path /path/to/kv
```

### 3. `regression` - 性能回归分析

查看历史性能数据和趋势：

```bash
python3 -m smartbench.cli regression [OPTIONS]

# 参数说明
--days               分析天数 (默认: 7)
--metric, -m        指标类型: qps, avg_latency, p99_latency, error_rate
```

**示例**:

```bash
# 查看 7 天 QPS 趋势
python3 -m smartbench.cli regression

# 查看延迟趋势
python3 -m smartbench.cli regression --metric avg_latency

# 查看 30 天趋势
python3 -m smartbench.cli regression --days 30
```

**输出示例**:

```
╭──────────────╮
│ 性能回归分析 │
╰──────────────╯

📈 QPS 趋势分析
   当前值: 387.75
   起始值: 292.30
   变化率: +32.7%
   趋势: improving

最近 5 次测试记录:
┏━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ 时间             ┃ QPS ┃  延迟 ┃ 错误率 ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━╇━━━━━━━━┩
│ 2026-04-13 20:08 │ 388 │ 2.4ms │  0.00% │
│ 2026-04-13 20:04 │ 292 │ 3.0ms │  0.00% │
└──────────────────┴─────┴───────┴────────┘
```

### 4. `analyze` - 建议代码分析

分析优化建议的代码位置和可行性：

```bash
python3 -m smartbench.cli analyze [OPTIONS]

# 参数说明
--project-path       项目路径 (默认: /home/xianyu-sheng/MyKV_storageBase_Raft_cpp)
--suggestions-file   建议文件路径 (JSON 格式)
```

**示例**:

```bash
# 分析测试建议
python3 -m smartbench.cli analyze

# 从文件分析建议
python3 -m smartbench.cli analyze --suggestions-file suggestions.json
```

### 5. `stats` - 模型统计

显示各模型的采纳率和准确率：

```bash
python3 -m smartbench.cli stats
```

### 6. `export` - 导出配置

导出配置模板：

```bash
python3 -m smartbench.cli export --output my_config.yaml --format yaml
```

---

## 配置详解

### 配置文件位置

| 环境 | 配置文件 |
|------|----------|
| 默认 | `config/default.yaml` |
| 开发 | `config/dev.yaml` |

### 模型配置

编辑 `config/default.yaml`:

```yaml
models:
  - name: "deepseek"
    provider: "openai_compatible"
    api_key: "${DEEPSEEK_API_KEY}"
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    model: "deepseek-v3-2-251201"
    enabled: true
    default_weight: 1.0
    max_retries: 3
    timeout: 60

  - name: "glm-4.7"
    provider: "openai_compatible"
    api_key: "${GLM_API_KEY}"
    base_url: "https://open.bigmodel.cn/api/paas/v4"
    model: "glm-4-0520"
    enabled: true
    default_weight: 0.9
    max_retries: 3
    timeout: 60

  - name: "doubao-seed-code"
    provider: "openai_compatible"
    api_key: "${DOUBAO_API_KEY}"
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    model: "doubao-seed-2.0-code"
    enabled: true
    default_weight: 0.8
    max_retries: 3
    timeout: 60

  - name: "doubao-seed-pro"
    provider: "openai_compatible"
    api_key: "${DOUBAO_API_KEY}"
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    model: "doubao-seed-2.0-pro"
    enabled: true
    default_weight: 0.85
    max_retries: 3
    timeout: 60
```

### 系统配置

```yaml
systems:
  raft_kv:
    name: "raft_kv"
    system_type: "raft_kv"
    project_path: "/home/xianyu-sheng/MyKV_storageBase_Raft_cpp"
    default_target_qps: 400

    custom_settings:
      benchmark_command: "./test_fast_bench.sh"
      full_benchmark_command: "./test_stable_bench.sh"
      qps_range: [300, 400, 500, 600]
      ops_per_test: 100
      optimal_threads: 4
      warmup_ops: 50
      leader_wait_timeout: 15
```

---

## 项目结构

```
SmartBench/
├── smartbench/
│   ├── cli.py                    # CLI 入口
│   ├── core/                    # 核心模块
│   │   ├── config.py            # 配置管理
│   │   └── types.py             # 数据类型定义
│   ├── engine/                  # 分析引擎
│   │   ├── debate.py            # 辩论引擎 (Proposer/Critique/Judge)
│   │   ├── cache.py             # 代码缓存
│   │   ├── regression.py        # 性能回归分析
│   │   ├── compiler.py          # 代码分析器
│   │   ├── aggregator.py        # 建议聚合器
│   │   ├── weight.py            # 权重引擎
│   │   └── generator.py         # 报告生成器
│   ├── agents/                  # Agent 模块
│   │   ├── benchmark.py         # Benchmark Agent
│   │   ├── observer.py          # Observer Agent
│   │   ├── analysis.py          # Analysis Agent
│   │   └── orchestrator.py     # Orchestrator Agent
│   └── plugins/                 # 插件系统
│       ├── models/              # 模型插件
│       │   ├── base.py
│       │   ├── openai_compat.py
│       │   └── anthropic.py
│       └── systems/             # 系统插件
│           ├── base.py
│           └── raft_kv.py
├── config/                      # 配置文件
│   └── default.yaml
├── data/                       # 数据目录
│   ├── cache/                  # 代码缓存
│   └── regression/             # 性能历史
├── MyKV_storageBase_Raft_cpp/  # 被测项目 (用户代码库)
│   ├── Raft/
│   ├── KvServer/
│   ├── Clerk/
│   └── ...
├── test_fast_bench.sh          # 快速压测脚本
├── test_stable_bench.sh        # 稳定压测脚本
├── run.sh                      # 启动脚本
└── README.md
```

---

## 架构设计

### 工作流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SmartBench 执行流程                          │
└─────────────────────────────────────────────────────────────────────┘

     ┌──────────────┐
     │ 1. 压测系统   │
     │ 启动集群      │
     │ 执行压测      │
     └──────┬───────┘
            │
            ▼
     ┌──────────────┐
     │ 2. 采集数据   │
     │ 获取指标     │
     │ 收集日志     │
     │ 读取源码     │
     └──────┬───────┘
            │
            ▼
     ┌──────────────────────────────────────────────────┐
     │ 3. 辩论引擎 (多 Agent 协作)                        │
     ├──────────────────────────────────────────────────┤
     │                                                  │
     │   ┌─────────────┐                               │
     │   │  Proposer   │  分析问题                     │
     │   │  提出方案   │  生成优化建议                  │
     │   └──────┬──────┘                               │
     │          │                                       │
     │          ▼                                       │
     │   ┌─────────────┐                               │
     │   │   Critique  │  审查风险                     │
     │   │  交叉审查   │  评估可行性                    │
     │   └──────┬──────┘                               │
     │          │                                       │
     │          ▼                                       │
     │   ┌─────────────┐                               │
     │   │    Judge    │  综合意见                     │
     │   │  最终决策   │  输出建议                      │
     │   └──────┬──────┘                               │
     │          │                                       │
     └──────────┼───────────────────────────────────────┘
                │
                ▼
     ┌──────────────┐
     │ 4. 聚合输出   │
     │ 去重排序     │
     │ 生成报告     │
     └──────────────┘
```

### 辩论引擎详解

#### Proposer (方案提出者)

**职责**: 分析性能指标、日志、源码，识别瓶颈并提出方案

**输入**:
- 当前 QPS、目标 QPS
- 平均延迟、P99 延迟
- 服务器日志
- 关键源码片段

**输出**:
```json
{
  "analysis": "根因分析（100字以内）",
  "proposals": [
    {
      "title": "方案标题（10字以内）",
      "location": "文件路径:行号",
      "problem": "具体问题描述",
      "solution": "具体修改的代码",
      "expected_gain": "预期收益量化描述",
      "priority": 1-5,
      "risk_level": "low/medium/high"
    }
  ]
}
```

#### Critique (交叉审查者)

**职责**: 审查方案风险，确保安全性

**审查维度**:
- 是否可能引入新 bug
- 是否可能导致数据不一致
- 线程安全是否受影响
- 是否符合 Raft 协议

**输出**:
```json
{
  "verdicts": [
    {
      "proposal_title": "对应的方案标题",
      "verdict": "accept/modify/reject",
      "concerns": ["问题1", "问题2"],
      "suggestions": "修改建议（如果有）"
    }
  ],
  "overall_assessment": "总体评价"
}
```

#### Judge (最终仲裁者)

**职责**: 综合 Proposer 和 Critique 的意见，做出最终决策

**输出**:
```json
{
  "decision": "accepted/rejected/mixed",
  "reasoning": "决策理由",
  "final_suggestions": [
    {
      "title": "方案标题",
      "description": "问题分析",
      "implementation": "实施步骤",
      "location": "文件路径:行号",
      "priority": 1-5,
      "risk_level": "low/medium/high",
      "consensus": "模型共识程度"
    }
  ]
}
```

---

## 代码缓存机制

### 工作原理

```
首次读取:
  文件 A ──▶ 读取内容 ──▶ 计算 MD5 ──▶ 存入缓存

后续读取:
  文件 A ──▶ 计算 MD5 ──▶ 对比缓存 ──▶ 命中? 直接返回缓存
                                          │
                                          └── 未命中? 重新读取，更新缓存
```

### 缓存策略

| 操作 | 说明 |
|------|------|
| `read_file(path)` | 读取文件，使用缓存 |
| `get_snippet(path, start, end)` | 获取代码片段 |
| `get_key_files(project)` | 获取关键源码文件 |
| `cache_analysis(...)` | 缓存分析结果 |

### 缓存位置

```
data/cache/
├── file_cache.json      # 文件内容缓存
├── analysis_cache.json  # 分析结果缓存
└── stats.json           # 统计信息
```

---

## 性能回归分析

### 数据结构

```python
@dataclass
class PerformanceSnapshot:
    timestamp: str          # ISO 格式时间戳
    qps: float             # 吞吐量
    avg_latency: float    # 平均延迟
    p99_latency: float    # P99 延迟
    error_rate: float     # 错误率
    target_qps: float     # 目标 QPS
    notes: str             # 备注
```

### 趋势判断

| 趋势 | 判断条件 | 图标 |
|------|----------|------|
| improving | 变化 > 5% | 📈 |
| stable | -5% <= 变化 <= 5% | ➡️ |
| degrading | 变化 < -5% | 📉 |

### 回归检测

| 严重程度 | 条件 |
|----------|------|
| severe | QPS 下降 > 20% 或错误率上升 > 5% |
| moderate | QPS 下降 > 10% 或错误率上升 > 2% |
| minor | QPS 下降 > 5% 或错误率上升 > 1% |

---

## 代码分析器

### 功能

1. **位置验证**: 验证建议中的代码位置是否存在
2. **上下文提取**: 提取原始代码及上下文
3. **语法检查**: 检查括号匹配、分号等基本语法
4. **风险提示**: 识别潜在问题（如死锁风险）

### 分析维度

| 维度 | 说明 |
|------|------|
| 位置有效性 | 文件是否存在，行号是否有效 |
| 括号匹配 | {}、()、[] 是否匹配 |
| 线程安全 | 是否涉及锁操作 |
| 协议合规 | 是否符合 Raft 协议 |

---

## 扩展开发

### 添加新系统插件

```python
from smartbench.plugins.systems.base import BaseSystemPlugin
from smartbench.core.types import Metrics, SystemType

class MySystemPlugin(BaseSystemPlugin):
    @property
    def name(self) -> str:
        return "my_system"

    @property
    def system_type(self) -> SystemType:
        return SystemType.GENERIC

    def get_metrics(self) -> Metrics:
        # 实现压测逻辑
        return Metrics(qps=100.0, avg_latency=10.0, p99_latency=50.0, error_rate=0.0)
```

### 添加新模型插件

```python
from smartbench.plugins.models.base import BaseModelPlugin
from smartbench.core.types import AnalysisResult, AnalysisContext

class MyModelPlugin(BaseModelPlugin):
    @property
    def name(self) -> str:
        return "my_model"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        # 实现分析逻辑
        return AnalysisResult(model_name=self.name, suggestions=[])
```

---

## 常见问题

### Q1: 压测失败怎么办？

```bash
# 1. 检查 ZooKeeper 是否运行
zkServer.sh status

# 2. 检查日志
cat /home/xianyu-sheng/MyKV_storageBase_Raft_cpp/build/kvserver0.log

# 3. 手动启动集群
cd /home/xianyu-sheng/MyKV_storageBase_Raft_cpp/build
pkill -9 kvserver 2>/dev/null || true
./kvserver -i ../myRPC/conf/myrpc_0.conf &
./kvserver -i ../myRPC/conf/myrpc_1.conf &
./kvserver -i ../myRPC/conf/myrpc_2.conf &
```

### Q2: API 调用失败？

```bash
# 1. 检查 API Key
echo $DEEPSEEK_API_KEY

# 2. 测试 API 连通性
curl -X POST https://ark.cn-beijing.volces.com/api/v3/chat/completions \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v3-2-251201","messages":[{"role":"user","content":"hi"}]}'
```

### Q3: 如何清理缓存？

```bash
rm -rf /home/xianyu-sheng/SmartBench/data/cache/*
rm -rf /home/xianyu-sheng/SmartBench/data/regression/*
```

---

## 更新日志

### v0.3 (2026-04-13)
- ✅ 新增辩论引擎 (Proposer/Critique/Judge)
- ✅ 新增代码缓存机制
- ✅ 新增性能回归分析
- ✅ 新增建议代码位置验证
- ✅ 移除修改被测项目代码的功能
- ✅ 支持多模型并行分析

### v0.2 (2026-04-10)
- ✅ 支持多模型协作
- ✅ 实现权重引擎
- ✅ 实现建议聚合去重

### v0.1 (2026-04-08)
- ✅ 基础压测功能
- ✅ Raft KV 插件

---

## License

MIT
