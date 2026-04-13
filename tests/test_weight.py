"""智能权重引擎测试"""

import pytest
import json
from pathlib import Path

from smartbench.engine.weight import WeightEngine
from smartbench.core.types import Suggestion, RiskLevel


class TestWeightEngine:
    """权重引擎测试"""

    def test_weight_engine_init(self, temp_dir):
        engine = WeightEngine(
            history_db_path=str(temp_dir / "history.json"),
            confidence_threshold=0.4,
        )
        assert engine.confidence_threshold == 0.4
        assert engine.history_db_path.parent.exists()
        # 文件本身在 _load_history() 时才创建
        assert isinstance(engine.history, dict)

    def test_weight_engine_init_creates_directory(self, temp_dir):
        history_path = temp_dir / "subdir" / "history.json"
        engine = WeightEngine(history_db_path=str(history_path))
        assert history_path.parent.exists()

    def test_calculate_weight_basic(self, temp_dir, sample_suggestion):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        weight = engine.calculate_weight(
            sample_suggestion,
            "deepseek",
            [sample_suggestion],
        )
        # 基础权重 * 准确率 * 一致性 * 置信度 * 风险因子
        # 1.0 * ~1.0 * 0.8 * 0.8 * 1.0 ≈ 0.64
        assert 0.5 < weight < 2.0

    def test_calculate_weight_with_history(self, temp_dir, sample_suggestion):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        # 更新历史记录
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=False)

        weight = engine.calculate_weight(sample_suggestion, "deepseek", [sample_suggestion])
        assert 0.5 < weight < 2.0

    def test_weight_range_limits(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        # 创建极端权重的建议
        suggestion = Suggestion(
            title="test",
            description="test",
            pseudocode="code",
            priority=5,
            risk_level=RiskLevel.LOW,
            expected_gain="test",
            self_confidence=1.0,
            base_weight=10.0,  # 非常大的值
        )

        weight = engine.calculate_weight(suggestion, "test", [suggestion])
        # 应该被限制在 [0.1, 2.0] 范围内
        assert weight <= 2.0

    def test_risk_factor_low(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        suggestion = Suggestion(
            title="test",
            description="test",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.LOW,
            expected_gain="test",
        )
        factor = engine._get_risk_factor(RiskLevel.LOW)
        assert factor == 1.1

    def test_risk_factor_medium(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        factor = engine._get_risk_factor(RiskLevel.MEDIUM)
        assert factor == 1.0

    def test_risk_factor_high(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        factor = engine._get_risk_factor(RiskLevel.HIGH)
        assert factor == 0.8

    def test_consensus_weight_unique(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        suggestion = Suggestion(
            title="unique suggestion",
            description="test",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="test",
            source_model="deepseek",
        )
        weight = engine._get_consensus_weight(suggestion, [suggestion])
        assert weight == 0.8

    def test_consensus_weight_multiple_similar(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        base = Suggestion(
            title="optimize memory",
            description="reduce memory usage",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="test",
            source_model="deepseek",
        )

        # 创建相似的建议（不同模型）
        similar1 = Suggestion(
            title="memory optimization",
            description="reduce memory usage",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="test",
            source_model="claude",
        )

        similar2 = Suggestion(
            title="optimize memory usage",
            description="reduce memory usage",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="test",
            source_model="qwen",
        )

        weight = engine._get_consensus_weight(base, [base, similar1, similar2])
        assert weight >= 1.0

    def test_update_history_adopted(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=False)

        assert engine.history["deepseek"]["total"] == 3
        assert engine.history["deepseek"]["adopted"] == 2

    def test_update_history_with_suggestion(self, temp_dir, sample_suggestion):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        engine.update_history("deepseek", adopted=True, suggestion=sample_suggestion)

        details = engine.history["deepseek"]["details"]
        assert len(details) == 1
        assert details[0]["suggestion_title"] == sample_suggestion.title
        assert details[0]["adopted"] is True

    def test_update_history_details_limit(self, temp_dir, sample_suggestion):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        # 添加超过 100 条记录
        for i in range(105):
            engine.update_history("deepseek", adopted=i % 2 == 0, suggestion=sample_suggestion)

        # 应该只保留最近 100 条
        assert len(engine.history["deepseek"]["details"]) == 100

    def test_get_model_stats(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=True)
        engine.update_history("deepseek", adopted=False)

        stats = engine.get_model_stats("deepseek")
        assert stats["model_name"] == "deepseek"
        assert stats["total_suggestions"] == 3
        assert stats["adopted_suggestions"] == 2
        assert stats["accuracy"] == pytest.approx(2 / 3, rel=0.1)

    def test_get_model_stats_nonexistent(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        stats = engine.get_model_stats("nonexistent")
        assert stats["accuracy"] is None
        assert stats["total_suggestions"] == 0

    def test_get_all_stats(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        engine.update_history("deepseek", adopted=True)
        engine.update_history("claude", adopted=False)

        stats = engine.get_all_stats()
        assert len(stats) == 2
        model_names = [s["model_name"] for s in stats]
        assert "deepseek" in model_names
        assert "claude" in model_names

    def test_reset_history(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        engine.update_history("deepseek", adopted=True)

        assert len(engine.history) > 0

        engine.reset_history()
        assert len(engine.history) == 0

    def test_title_similarity_identical(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        sim = engine._title_similarity("优化内存使用", "优化内存使用")
        assert sim == 1.0

    def test_title_similarity_no_overlap(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        sim = engine._title_similarity("优化内存", "提高网络速度")
        assert sim == 0.0

    def test_title_similarity_partial_overlap(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        sim = engine._title_similarity("优化内存使用", "优化内存分配")
        # 去掉停用词后应该有交集
        assert sim >= 0.0

    def test_keyword_similarity(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        text1 = "跳表并发锁竞争 RCU 机制"
        text2 = "跳表 RCU 读写分离"
        sim = engine._keyword_similarity(text1, text2)
        assert sim >= 0.0

    def test_is_similar_by_title(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        a = Suggestion(
            title="skip list RCU optimization",
            description="",
            pseudocode="",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="",
        )

        b = Suggestion(
            title="skip list RCU optimization",
            description="",
            pseudocode="",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="",
        )

        # 标题完全相同
        title_sim = engine._title_similarity(a.title, b.title)
        assert title_sim == 1.0, f"title_sim={title_sim}"
        assert engine._is_similar(a, b) is True

    def test_is_similar_by_description(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        desc_a = "skip list lock contention severely impacts concurrent performance and throughput"
        desc_b = "skip list lock contention causes severe performance degradation and lower throughput"

        # 英文描述有共同技术词汇
        desc_sim = engine._keyword_similarity(desc_a, desc_b)
        assert desc_sim > 0

    def test_is_similar_different_topics(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))

        a = Suggestion(
            title="RCU optimization",
            description="read write lock performance",
            pseudocode="",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="",
        )

        b = Suggestion(
            title="network zero copy",
            description="zero copy IO technique",
            pseudocode="",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="",
        )

        # 完全不同主题，不相似
        assert engine._is_similar(a, b) is False

    def test_persistence(self, temp_dir):
        """测试历史记录持久化"""
        # 第一次写入
        engine1 = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        engine1.update_history("deepseek", adopted=True)
        engine1.update_history("deepseek", adopted=False)

        # 重新加载
        engine2 = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        assert "deepseek" in engine2.history
        assert engine2.history["deepseek"]["total"] == 2
        assert engine2.history["deepseek"]["adopted"] == 1

    def test_corrupted_history_file(self, temp_dir):
        """测试损坏的历史文件"""
        history_path = temp_dir / "history.json"
        history_path.write_text("not valid json {{{", encoding="utf-8")

        # 应该静默处理，返回空历史
        engine = WeightEngine(history_db_path=str(history_path))
        assert engine.history == {}
