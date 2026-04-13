"""Pytest 配置和共享 fixtures"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from smartbench.core.types import Metrics, Suggestion, RiskLevel, AnalysisResult


@pytest.fixture
def temp_dir():
    """创建临时目录用于测试"""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_metrics():
    """创建示例性能指标"""
    return Metrics(
        qps=250.0,
        avg_latency=12.5,
        p50_latency=10.0,
        p99_latency=50.0,
        error_rate=0.005,
    )


@pytest.fixture
def sample_suggestion():
    """创建示例优化建议"""
    return Suggestion(
        title="使用跳表并发优化",
        description="当前跳表读操作存在锁竞争，建议引入 RCU 机制提升并发读性能。",
        pseudocode="""// 使用 RCU 机制优化并发读
class SkipListRCU {
    std::atomic<Node*> head;
public:
    Value* read(Key key) {
        rcu_read_lock();
        Node* n = head.load();
        while (n && n->key < key)
            n = n->next[0];
        rcu_read_unlock();
        return n ? n->value : nullptr;
    }
}""",
        priority=5,
        risk_level=RiskLevel.MEDIUM,
        expected_gain="读 QPS 提升 20-30%",
        implementation_steps=[
            "引入 rcu库",
            "改造跳表节点结构",
            "实现读锁分离",
        ],
        source_model="deepseek",
        self_confidence=0.8,
        base_weight=1.0,
    )


@pytest.fixture
def sample_suggestions():
    """创建多个示例建议"""
    return [
        Suggestion(
            title="优化日志输出频率",
            description="当前日志输出过于频繁，影响性能。",
            pseudocode="# 降低日志级别",
            priority=4,
            risk_level=RiskLevel.LOW,
            expected_gain="QPS 提升 5%",
            source_model="deepseek",
            self_confidence=0.9,
            base_weight=1.0,
        ),
        Suggestion(
            title="增加批处理大小",
            description="当前批处理大小过小，增加网络往返次数。",
            pseudocode="# batch_size = 128",
            priority=5,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="QPS 提升 15%",
            source_model="claude",
            self_confidence=0.7,
            base_weight=1.2,
        ),
        Suggestion(
            title="使用连接池",
            description="频繁创建销毁连接，增加开销。",
            pseudocode="# 连接池复用",
            priority=3,
            risk_level=RiskLevel.HIGH,
            expected_gain="QPS 提升 10%",
            source_model="deepseek",
            self_confidence=0.6,
            base_weight=1.0,
        ),
    ]


@pytest.fixture
def sample_analysis_result(sample_suggestions):
    """创建示例分析结果"""
    return [
        AnalysisResult(
            model_name="deepseek",
            suggestions=sample_suggestions[:2],
            raw_response="...",
            processing_time=3.5,
        ),
        AnalysisResult(
            model_name="claude",
            suggestions=sample_suggestions[1:3],
            raw_response="...",
            processing_time=5.2,
        ),
    ]


@pytest.fixture
def sample_yaml_config(temp_dir):
    """创建示例 YAML 配置文件"""
    config_path = temp_dir / "test_config.yaml"
    # 创建模拟的项目目录（因为 SystemConfig 会验证路径）
    mock_project = temp_dir / "test_project"
    mock_project.mkdir()
    config_content = f"""
output_dir: "{temp_dir / 'output'}"
data_dir: "{temp_dir / 'data'}"

weight_engine:
  confidence_threshold: 0.35
  default_model_weight: 0.75
  max_suggestions: 6
  history_db_path: "{temp_dir / 'data' / 'history.json'}"

models:
  - name: "deepseek"
    provider: "openai_compatible"
    api_key: "test-key-123"
    base_url: "https://api.deepseek.com"
    model: "deepseek-chat"
    default_weight: 0.8
    enabled: true
    max_retries: 3
    timeout: 60

systems:
  - name: "raft_kv"
    system_type: "raft_kv"
    project_path: "{mock_project}"
    benchmark_command: "./bench.sh"
    log_path: "./build"
"""
    config_path.write_text(config_content, encoding="utf-8")
    return config_path
