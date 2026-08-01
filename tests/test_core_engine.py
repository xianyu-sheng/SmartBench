"""
Tests for the unified diagnostic framework engine.
"""

import tempfile
from pathlib import Path

import pytest

from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    UnifiedDiagnosticResult,
)
from smartbench.core.adapters import PythonAdapter
from smartbench.core.rules import NullDereferenceRule, ResourceLeakRule
from smartbench.core.rules.base import DiagnosticRule, Finding, Location, Severity


class TestUnifiedDiagnosticConfig:
    def test_defaults(self):
        config = UnifiedDiagnosticConfig()
        assert config.use_llm_rules is False
        assert config.use_static_rules is True
        assert config.max_files == 500
        assert config.rule_ids is None
        assert config.languages is None
        assert config.min_confidence == 0.7

    def test_invalid_confidence_threshold(self):
        with pytest.raises(ValueError, match="min_confidence"):
            UnifiedDiagnosticConfig(min_confidence=1.1)

    def test_custom_config(self):
        config = UnifiedDiagnosticConfig(
            use_llm_rules=True,
            languages=["python", "go"],
            rule_ids=["null_dereference"],
        )
        assert config.use_llm_rules is True
        assert "python" in config.languages


class TestUnifiedDiagnosticResult:
    def test_defaults(self):
        result = UnifiedDiagnosticResult()
        assert len(result.findings) == 0
        assert len(result.errors) == 0
        assert result.duration_ms == 0

    def test_to_dict(self):
        result = UnifiedDiagnosticResult()
        d = result.to_dict()
        assert "findings" in d
        assert "stats" in d
        assert "errors" in d


class TestUnifiedDiagnosticEngine:
    def setup_method(self):
        self.adapters = AdapterRegistry()
        self.adapters.register(PythonAdapter())

        self.rules = RuleRegistry()
        self.rules.register(NullDereferenceRule())
        self.rules.register(ResourceLeakRule())

        self.engine = UnifiedDiagnosticEngine(self.adapters, self.rules)

    def test_create_engine(self):
        assert self.engine is not None

    def test_diagnose_empty_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = UnifiedDiagnosticConfig()
            result = self.engine.diagnose(Path(tmpdir), config)

            # Should complete without errors
            assert result is not None

    def test_diagnose_python_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create a test Python file
            test_file = tmp_path / "test.py"
            test_file.write_text("""
def test_func():
    x = None
    # This might trigger a pattern match
    return 42

# Resource leak pattern candidate
f = open("test.txt", "w")
f.write("hello")
""")

            config = UnifiedDiagnosticConfig()
            result = self.engine.diagnose_file(test_file, tmp_path, config)

            # Should complete without errors
            assert result is not None
            assert "Diagnostic failed:" not in result.errors

    def test_config_rule_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_file = tmp_path / "test.py"
            test_file.write_text("def test(): pass")

            config = UnifiedDiagnosticConfig(rule_ids=["null_dereference"])
            result = self.engine.diagnose_file(test_file, tmp_path, config)

            assert result is not None

    def test_confidence_filter_is_applied(self):
        class ConfidenceRule(DiagnosticRule):
            @property
            def rule_id(self):
                return "confidence_test"

            @property
            def rule_name(self):
                return "Confidence Test"

            def analyze(self, ir):
                return [
                    Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=Severity.WARNING,
                        location=Location("test.py", 1),
                        message="low",
                        confidence=0.6,
                    ),
                    Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=Severity.WARNING,
                        location=Location("test.py", 2),
                        message="high",
                        confidence=0.9,
                    ),
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_file = tmp_path / "test.py"
            test_file.write_text("value = 1")
            rules = RuleRegistry()
            rules.register(ConfidenceRule())
            engine = UnifiedDiagnosticEngine(self.adapters, rules)

            result = engine.diagnose_file(
                test_file,
                tmp_path,
                UnifiedDiagnosticConfig(min_confidence=0.8),
            )

            assert [finding.message for finding in result.findings] == ["high"]

    def test_disabled_rule_requires_explicit_selection(self):
        class DisabledRule(DiagnosticRule):
            enabled_by_default = False

            @property
            def rule_id(self):
                return "disabled_test"

            @property
            def rule_name(self):
                return "Disabled Test"

            def analyze(self, ir):
                return [
                    Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=Severity.WARNING,
                        location=Location("test.py", 1),
                        message="explicit only",
                    )
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_file = tmp_path / "test.py"
            test_file.write_text("value = 1")
            rules = RuleRegistry()
            rules.register(DisabledRule())
            engine = UnifiedDiagnosticEngine(self.adapters, rules)

            default_result = engine.diagnose_file(test_file, tmp_path)
            explicit_result = engine.diagnose_file(
                test_file,
                tmp_path,
                UnifiedDiagnosticConfig(rule_ids=["disabled_test"]),
            )

            assert default_result.findings == []
            assert [finding.message for finding in explicit_result.findings] == [
                "explicit only"
            ]


class TestFrontendParseErrorPropagation:
    """Regression tests for issue #1: unparseable file must not yield a clean report."""

    def setup_method(self):
        from smartbench.cli.unified import setup_engine
        self.engine = setup_engine()

    def test_python_syntax_error_surfaces_in_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "bad.py"
            bad.write_text("def broken(:\n    pass\n")
            config = UnifiedDiagnosticConfig()
            result = self.engine.diagnose(Path(tmpdir), config)
            # Parse error must be recorded, not silently dropped
            assert any("bad.py" in e for e in result.errors), (
                "Expected a parse-error entry for bad.py in result.errors, got: "
                + repr(result.errors)
            )


    def test_stats_ir_parse_errors_count_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("def broken(:\n    pass\n")
            (Path(tmpdir) / "b.py").write_text("def ok():\n    pass\n")
            config = UnifiedDiagnosticConfig()
            result = self.engine.diagnose(Path(tmpdir), config)
            report = result.to_dict()
            assert report["stats"]["ir_parse_errors"] >= 1, (
                "stats.ir_parse_errors should count the unparseable file"
            )

    def test_valid_file_in_same_project_still_analyzed(self):
        """A parse error in one file must not abort analysis of other files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bad.py").write_text("def broken(:\n    pass\n")
            good = Path(tmpdir) / "good.py"
            good.write_text('PASSWORD = "s3cr3t_h4rdcoded"\n')
            config = UnifiedDiagnosticConfig()
            result = self.engine.diagnose(Path(tmpdir), config)
            # Error recorded AND other files still analyzed
            assert any("bad.py" in e for e in result.errors)
            rule_ids = {f.rule_id for f in result.findings}
            assert "hardcoded_secret" in rule_ids, (
                "good.py should still be analyzed despite bad.py failing"
            )
