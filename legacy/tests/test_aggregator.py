"""建议聚合器测试"""

import pytest
from smartbench.engine.aggregator import SuggestionAggregator
from smartbench.engine.weight import WeightEngine
from smartbench.core.types import Suggestion, AnalysisResult, RiskLevel


class TestSuggestionAggregator:
    """聚合器测试"""

    def test_aggregator_init(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(
            weight_engine=engine,
            confidence_threshold=0.3,
            similarity_threshold=0.75,
        )
        assert aggregator.confidence_threshold == 0.3
        assert aggregator.similarity_threshold == 0.75

    def test_aggregate_empty_results(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)
        results = []
        suggestions = aggregator.aggregate(results)
        assert suggestions == []

    def test_aggregate_single_result(self, temp_dir, sample_suggestions):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        result = AnalysisResult(
            model_name="deepseek",
            suggestions=sample_suggestions[:1],
            processing_time=3.0,
        )

        suggestions = aggregator.aggregate([result])
        assert isinstance(suggestions, list)

    def test_aggregate_multiple_results(self, temp_dir, sample_analysis_result):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        suggestions = aggregator.aggregate(sample_analysis_result)
        assert isinstance(suggestions, list)

    def test_aggregate_max_suggestions(self, temp_dir, sample_analysis_result):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        suggestions = aggregator.aggregate(sample_analysis_result, max_suggestions=2)
        assert len(suggestions) <= 2

    def test_aggregate_failed_results_ignored(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        failed_result = AnalysisResult(
            model_name="deepseek",
            error="API timeout",
        )

        suggestions = aggregator.aggregate([failed_result])
        assert suggestions == []

    def test_collect_suggestions(self, temp_dir, sample_analysis_result):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        suggestions = aggregator._collect_suggestions(sample_analysis_result)
        assert len(suggestions) >= 3

    def test_calculate_weights(self, temp_dir, sample_suggestions):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        weighted = aggregator._calculate_weights(sample_suggestions)
        for s in weighted:
            assert s.final_weight > 0

    def test_deduplicate_identical(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(
            weight_engine=engine,
            similarity_threshold=0.6,
        )

        s1 = Suggestion(
            title="内存优化",
            description="使用内存池",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="20%",
            source_model="deepseek",
        )
        s1.final_weight = 0.9

        s2 = Suggestion(
            title="内存优化",
            description="使用内存池",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="20%",
            source_model="claude",
        )
        s2.final_weight = 1.1

        deduplicated = aggregator._deduplicate([s1, s2])
        assert len(deduplicated) == 1
        assert deduplicated[0].source_model.startswith("claude")

    def test_deduplicate_different(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        s1 = Suggestion(
            title="memory optimization",
            description="memory pool approach",
            pseudocode="code1",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="",
            source_model="deepseek",
        )
        s1.final_weight = 0.8

        s2 = Suggestion(
            title="network optimization",
            description="zero copy technique",
            pseudocode="code2",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="",
            source_model="claude",
        )
        s2.final_weight = 0.9

        suggestions = [s1, s2]
        deduplicated = aggregator._deduplicate(suggestions)
        assert len(deduplicated) == 2

    def test_sort_by_priority(self, temp_dir, sample_suggestions):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        for i, s in enumerate(sample_suggestions):
            s.final_weight = 1.0 - i * 0.1

        sorted_suggestions = aggregator._sort_by_priority(sample_suggestions)

        risk_order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        for i in range(len(sorted_suggestions) - 1):
            current_risk = risk_order.get(sorted_suggestions[i].risk_level, 1)
            next_risk = risk_order.get(sorted_suggestions[i + 1].risk_level, 1)
            if current_risk == next_risk:
                assert sorted_suggestions[i].final_weight >= sorted_suggestions[i + 1].final_weight

    def test_filter_low_quality(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(
            weight_engine=engine,
            confidence_threshold=0.5,
        )

        suggestions = [
            Suggestion(
                title="高质量建议",
                description="有详细描述",
                pseudocode="valid code",
                priority=4,
                risk_level=RiskLevel.LOW,
                expected_gain="显著提升",
            ),
            Suggestion(
                title="",
                description="描述",
                pseudocode="code",
                priority=3,
                risk_level=RiskLevel.LOW,
                expected_gain="",
            ),
            Suggestion(
                title="无伪代码",
                description="描述",
                pseudocode="# 无代码",
                priority=3,
                risk_level=RiskLevel.LOW,
                expected_gain="",
            ),
        ]

        suggestions[0].final_weight = 0.6
        suggestions[1].final_weight = 0.7
        suggestions[2].final_weight = 0.8

        filtered = aggregator._filter_low_quality(suggestions)
        assert len(filtered) == 1
        assert filtered[0].title == "高质量建议"

    def test_semantic_similarity_identical(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        a = Suggestion(
            title="memory optimization",
            description="use memory pool",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="reduce memory",
        )

        b = Suggestion(
            title="memory optimization",
            description="use memory pool",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="reduce memory",
        )

        sim = aggregator._semantic_similarity(a, b)
        assert 0.5 <= sim <= 1.0

    def test_semantic_similarity_different(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        a = Suggestion(
            title="memory optimization",
            description="use memory pool",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="reduce memory",
        )

        b = Suggestion(
            title="network optimization",
            description="zero copy technique",
            pseudocode="code",
            priority=3,
            risk_level=RiskLevel.MEDIUM,
            expected_gain="improve throughput",
        )

        sim = aggregator._semantic_similarity(a, b)
        assert 0.0 <= sim < 0.5

    def test_title_similarity(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        sim = aggregator._title_similarity(
            "memory optimization approach",
            "memory optimization method"
        )
        assert 0.0 < sim <= 1.0

        sim = aggregator._title_similarity("memory optimization", "network optimization")
        assert 0.0 <= sim < 0.5

    def test_keyword_similarity(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        sim = aggregator._keyword_similarity(
            "skip list concurrent lock contention rcu",
            "skip list rcu read write separation optimization",
        )
        assert 0.0 <= sim <= 1.0

    def test_get_summary(self, temp_dir, sample_suggestions):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        for s in sample_suggestions:
            s.final_weight = 0.8

        summary = aggregator.get_summary(sample_suggestions)

        assert summary["total"] == 3
        assert "by_risk" in summary
        assert "by_model" in summary
        assert summary["top_suggestion"] is not None

    def test_get_summary_empty(self, temp_dir):
        engine = WeightEngine(history_db_path=str(temp_dir / "history.json"))
        aggregator = SuggestionAggregator(weight_engine=engine)

        summary = aggregator.get_summary([])
        assert summary["total"] == 0
        assert summary["top_suggestion"] is None
