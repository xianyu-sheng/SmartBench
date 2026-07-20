"""核心数据类型测试"""

import pytest
from datetime import datetime

from smartbench.core.types import (
    RiskLevel,
    SystemType,
    ModelProvider,
    Metrics,
    Suggestion,
    AnalysisContext,
    AnalysisResult,
    OptimizationReport,
)


class TestRiskLevel:
    """风险等级枚举测试"""

    def test_risk_level_values(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"

    def test_risk_level_count(self):
        assert len(RiskLevel) == 3


class TestSystemType:
    """系统类型枚举测试"""

    def test_system_type_values(self):
        assert SystemType.GENERIC.value == "generic"
        assert SystemType.RAFT_KV.value == "raft_kv"
        assert SystemType.HTTP_API.value == "http_api"
        assert SystemType.DATABASE.value == "database"

    def test_system_type_count(self):
        assert len(SystemType) == 4


class TestModelProvider:
    """模型提供商枚举测试"""

    def test_model_provider_values(self):
        assert ModelProvider.OPENAI_COMPATIBLE.value == "openai_compatible"
        assert ModelProvider.ANTHROPIC.value == "anthropic"
        assert ModelProvider.DASHSCOPE.value == "dashscope"

    def test_model_provider_count(self):
        assert len(ModelProvider) == 3


class TestMetrics:
    """性能指标测试"""

    def test_metrics_creation(self):
        m = Metrics(
            qps=100.0,
            avg_latency=10.0,
            p50_latency=8.0,
            p99_latency=50.0,
            error_rate=0.01,
        )
        assert m.qps == 100.0
        assert m.avg_latency == 10.0
        assert m.p50_latency == 8.0
        assert m.p99_latency == 50.0
        assert m.error_rate == 0.01

    def test_metrics_defaults(self):
        m = Metrics(qps=100.0, avg_latency=10.0)
        assert m.p50_latency == 0.0
        assert m.p99_latency == 0.0
        assert m.error_rate == 0.0
        assert isinstance(m.timestamp, datetime)

    def test_is_healthy_healthy(self):
        m = Metrics(qps=100.0, avg_latency=10.0, error_rate=0.005)
        assert m.is_healthy() is True

    def test_is_healthy_unhealthy(self):
        m = Metrics(qps=100.0, avg_latency=10.0, error_rate=0.02)
        assert m.is_healthy() is False

    def test_gap_to_target_positive(self):
        m = Metrics(qps=200.0, avg_latency=10.0)
        gap = m.gap_to_target(300.0)
        assert gap == pytest.approx(33.33, rel=0.1)

    def test_gap_to_target_achieved(self):
        m = Metrics(qps=300.0, avg_latency=10.0)
        gap = m.gap_to_target(300.0)
        assert gap == 0.0

    def test_gap_to_target_zero_target(self):
        m = Metrics(qps=200.0, avg_latency=10.0)
        gap = m.gap_to_target(0.0)
        assert gap == 0.0


class TestSuggestion:
    """优化建议测试"""

    def test_suggestion_creation(self):
        s = Suggestion(
            title="测试建议",
            description="测试描述",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="10% 提升",
        )
        assert s.title == "测试建议"
        assert s.priority == 3
        assert s.risk_level == RiskLevel.MEDIUM
        assert s.final_weight == 0.0

    def test_suggestion_priority_clamp_valid(self):
        """验证优先级在有效范围内正常工作"""
        s = Suggestion(
            title="测试",
            description="测试",
            pseudocode="code",
            priority=4,
            risk_level=RiskLevel.LOW,
            expected_gain="",
        )
        assert s.priority == 4

    def test_suggestion_confidence_clamp_valid(self):
        """验证置信度在有效范围内正常工作"""
        s = Suggestion(
            title="测试",
            description="测试",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.LOW,
            expected_gain="",
            self_confidence=0.8,
        )
        assert s.self_confidence == 0.8

    def test_suggestion_to_dict(self):
        s = Suggestion(
            title="测试",
            description="描述",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.LOW,
            expected_gain="提升",
            implementation_steps=["步骤1", "步骤2"],
            source_model="deepseek",
            self_confidence=0.8,
            base_weight=1.0,
        )
        d = s.to_dict()
        assert d["title"] == "测试"
        assert d["risk_level"] == "low"
        assert d["priority"] == 3
        assert len(d["implementation_steps"]) == 2


class TestAnalysisContext:
    """分析上下文测试"""

    def test_analysis_context_creation(self, sample_metrics):
        ctx = AnalysisContext(
            system_name="raft_kv",
            system_type=SystemType.RAFT_KV,
            metrics=sample_metrics,
            logs="log content",
            source_code="code content",
            target_qps=300.0,
        )
        assert ctx.system_name == "raft_kv"
        assert ctx.system_type == SystemType.RAFT_KV
        assert ctx.logs == "log content"
        assert ctx.source_code == "code content"
        assert ctx.target_qps == 300.0

    def test_analysis_context_to_prompt_text(self, sample_metrics):
        ctx = AnalysisContext(
            system_name="raft_kv",
            system_type=SystemType.RAFT_KV,
            metrics=sample_metrics,
            logs="log line 1\nlog line 2",
            target_qps=300.0,
        )
        text = ctx.to_prompt_text()
        assert "raft_kv" in text
        assert "250.0" in text
        assert "log line 1" in text

    def test_analysis_context_to_prompt_truncation(self, sample_metrics):
        ctx = AnalysisContext(
            system_name="raft_kv",
            system_type=SystemType.RAFT_KV,
            metrics=sample_metrics,
            logs="x" * 10000,  # 超过 5000 字符
            source_code="y" * 10000,  # 超过 3000 字符
            target_qps=300.0,
        )
        text = ctx.to_prompt_text()
        assert len(text) < 20000  # 应该被截断


class TestAnalysisResult:
    """分析结果测试"""

    def test_analysis_result_success(self, sample_suggestions):
        result = AnalysisResult(
            model_name="deepseek",
            suggestions=sample_suggestions,
            raw_response="raw",
            processing_time=3.5,
        )
        assert result.is_success is True
        assert result.error is None

    def test_analysis_result_failure(self):
        result = AnalysisResult(
            model_name="deepseek",
            error="API timeout",
        )
        assert result.is_success is False
        assert result.error == "API timeout"

    def test_analysis_result_empty_suggestions(self):
        result = AnalysisResult(
            model_name="deepseek",
            suggestions=[],
            error=None,
        )
        assert result.is_success is False

    def test_analysis_result_defaults(self):
        result = AnalysisResult(model_name="test")
        assert result.suggestions == []
        assert result.raw_response == ""
        assert result.processing_time == 0.0
        assert result.error is None
        assert result.is_success is False


class TestOptimizationReport:
    """优化报告测试"""

    def test_optimization_report_creation(self, sample_metrics, sample_suggestions):
        report = OptimizationReport(
            timestamp=datetime.now(),
            target_system="raft_kv",
            current_metrics=sample_metrics,
            target_qps=300.0,
            suggestions=sample_suggestions,
            summary="测试报告",
            raw_data_path="/tmp/raw.json",
            report_path="/tmp/report.md",
        )
        assert report.target_system == "raft_kv"
        assert len(report.suggestions) == 3
        assert report.summary == "测试报告"

    def test_optimization_report_to_dict(self, sample_metrics):
        report = OptimizationReport(
            timestamp=datetime.now(),
            target_system="raft_kv",
            current_metrics=sample_metrics,
            target_qps=300.0,
        )
        d = report.to_dict()
        assert d["target_system"] == "raft_kv"
        assert d["target_qps"] == 300.0
        assert "current_metrics" in d
        assert "timestamp" in d
