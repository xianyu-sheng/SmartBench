"""
Tests for the unified diagnostic framework engine.
"""

import tempfile
from pathlib import Path

import pytest

from smartbench.core import (
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    UnifiedDiagnosticResult,
    AdapterRegistry,
    RuleRegistry,
)
from smartbench.core.adapters import PythonAdapter
from smartbench.core.rules import NullDereferenceRule, ResourceLeakRule


class TestUnifiedDiagnosticConfig:
    def test_defaults(self):
        config = UnifiedDiagnosticConfig()
        assert config.use_llm_rules is False
        assert config.use_static_rules is True
        assert config.max_files == 500
        assert config.rule_ids is None
        assert config.languages is None

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
