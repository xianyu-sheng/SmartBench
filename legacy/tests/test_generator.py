"""文档生成器测试"""

import pytest
import json
from pathlib import Path

from smartbench.engine.generator import DocumentGenerator
from smartbench.core.types import Suggestion, RiskLevel


class TestDocumentGenerator:
    """文档生成器测试"""

    def test_generator_init(self, temp_dir):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )
        assert generator.output_dir.exists()
        assert generator.data_dir.exists()

    def test_generator_creates_directories(self, temp_dir):
        output_dir = temp_dir / "reports" / "output"
        data_dir = temp_dir / "reports" / "data"
        generator = DocumentGenerator(
            output_dir=str(output_dir),
            data_dir=str(data_dir),
        )
        assert output_dir.exists()
        assert data_dir.exists()

    def test_generate_with_suggestions(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
            system_type="raft_kv",
        )

        assert report.target_system == "raft_kv"
        assert len(report.suggestions) == 3
        assert report.report_path.endswith(".md")
        assert report.raw_data_path.endswith(".json")
        assert Path(report.report_path).exists()
        assert Path(report.raw_data_path).exists()

    def test_generate_without_suggestions(self, temp_dir, sample_metrics):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=[],
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
            system_type="raft_kv",
        )

        assert len(report.suggestions) == 0
        assert "未发现明显性能瓶颈" in report.summary

    def test_generate_saves_raw_json(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
        )

        raw_path = Path(report.raw_data_path)
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        assert raw_data["system_name"] == "raft_kv"
        assert raw_data["target_qps"] == 300.0
        assert "metrics" in raw_data
        assert len(raw_data["suggestions"]) == 3

    def test_generate_saves_markdown(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
            system_type="raft_kv",
        )

        report_path = Path(report.report_path)
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 验证 Markdown 内容
        assert "# SmartBench 优化建议报告" in content
        assert "raft_kv" in content
        assert "性能分析摘要" in content
        assert "优化建议" in content

    def test_generate_summary_markdown(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        summary = generator.generate_summary_markdown(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
        )

        assert "优化建议摘要" in summary
        assert "250.0" in summary  # 当前 QPS
        assert "300" in summary  # 目标 QPS

    def test_generate_summary_markdown_empty(self, temp_dir, sample_metrics):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        summary = generator.generate_summary_markdown(
            suggestions=[],
            metrics=sample_metrics,
            target_qps=300.0,
        )

        assert "优化建议摘要" in summary

    def test_report_filename_format(self, temp_dir, sample_metrics):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=[],
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
        )

        # 文件名格式: report_YYYYMMDD_HHMMSS.md
        filename = Path(report.report_path).name
        assert filename.startswith("report_")
        assert filename.endswith(".md")
        # 检查时间戳格式
        assert "_" in filename
        parts = filename.replace("report_", "").replace(".md", "").split("_")
        assert len(parts) == 2  # 日期和时间部分
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS

    def test_metrics_in_summary(self, temp_dir, sample_metrics):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=[],
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
        )

        report_path = Path(report.report_path)
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 验证指标在报告中
        assert "250.0" in content  # QPS
        assert "12.5" in content  # 平均延迟
        assert "50.0" in content  # P99 延迟

    def test_suggestion_formatting(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
        )

        report_path = Path(report.report_path)
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 验证建议格式化
        assert "### 方案 1:" in content
        assert "### 方案 2:" in content
        assert "问题分析" in content
        assert "伪代码实现" in content
        assert "实施步骤" in content

    def test_risk_level_display(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
        )

        report_path = Path(report.report_path)
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 验证风险等级显示
        assert "low" in content
        assert "medium" in content
        assert "high" in content

    def test_implementation_section(self, temp_dir, sample_metrics, sample_suggestions):
        generator = DocumentGenerator(
            output_dir=str(temp_dir / "output"),
            data_dir=str(temp_dir / "data"),
        )

        report = generator.generate(
            suggestions=sample_suggestions,
            metrics=sample_metrics,
            target_qps=300.0,
            system_name="raft_kv",
        )

        report_path = Path(report.report_path)
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 验证实施建议部分
        assert "## 实施建议" in content
        assert "## 验证步骤" in content
        assert "低风险优化" in content or "中风险优化" in content or "高风险优化" in content
