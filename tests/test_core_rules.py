"""
Tests for the unified diagnostic framework rules.
"""

import tempfile
from pathlib import Path

import pytest

from smartbench.core.rules.base import (
    DiagnosticRule,
    Finding,
    Location,
    RuleRegistry,
    Severity,
)
from smartbench.core.rules.common import (
    NullDereferenceRule,
    ResourceLeakRule,
    register_builtin_rules,
)
from smartbench.graph.schema import CodeGraph, CodeNode, NodeType


class TestSeverity:
    def test_ordering(self):
        assert Severity.INFO < Severity.WARNING
        assert Severity.WARNING < Severity.ERROR

    def test_values(self):
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"


class TestLocation:
    def test_simple_location(self):
        loc = Location(file_path="main.py", line_start=10)
        assert loc.file_path == "main.py"
        assert loc.line_start == 10
        assert loc.line_end == 10

    def test_full_location(self):
        loc = Location(
            file_path="main.py",
            line_start=10,
            line_end=20,
            column_start=5,
            column_end=15,
        )
        assert loc.line_end == 20
        assert loc.column_start == 5

    def test_to_dict_roundtrip(self):
        loc = Location(
            file_path="main.py",
            line_start=10,
            line_end=20,
            column_start=5,
            column_end=15,
        )
        d = loc.to_dict()
        loc2 = Location.from_dict(d)
        assert loc2.file_path == loc.file_path
        assert loc2.line_start == loc.line_start


class TestFinding:
    def test_simple_finding(self):
        finding = Finding(
            rule_id="test_rule",
            rule_name="Test Rule",
            severity=Severity.WARNING,
            location=Location(file_path="main.py", line_start=10),
            message="Test message",
        )
        assert finding.rule_id == "test_rule"
        assert finding.severity == Severity.WARNING

    def test_to_dict_roundtrip(self):
        finding = Finding(
            rule_id="test_rule",
            rule_name="Test Rule",
            severity=Severity.ERROR,
            location=Location(file_path="main.py", line_start=10),
            message="Test message",
            confidence=0.9,
            metadata={"key": "value"},
        )
        d = finding.to_dict()
        finding2 = Finding.from_dict(d)
        assert finding2.rule_id == finding.rule_id
        assert finding2.message == finding.message


class TestRuleRegistry:
    def test_register_and_retrieve(self):
        class TestRule(DiagnosticRule):
            @property
            def rule_id(self) -> str:
                return "test_rule"

            @property
            def rule_name(self) -> str:
                return "Test Rule"

            def analyze(self, ir):
                return []

        registry = RuleRegistry()
        registry.register(TestRule())

        assert registry.get_rule("test_rule") is not None
        assert "test_rule" in registry.list_rule_ids()

    def test_register_duplicate_fails(self):
        class TestRule(DiagnosticRule):
            @property
            def rule_id(self) -> str:
                return "test_rule"

            @property
            def rule_name(self) -> str:
                return "Test Rule"

            def analyze(self, ir):
                return []

        registry = RuleRegistry()
        registry.register(TestRule())

        with pytest.raises(ValueError):
            registry.register(TestRule())

    def test_get_rules_for_language(self):
        class PythonOnlyRule(DiagnosticRule):
            @property
            def rule_id(self) -> str:
                return "py_rule"

            @property
            def rule_name(self) -> str:
                return "Python Rule"

            @property
            def supported_languages(self):
                return {"python"}

            def analyze(self, ir):
                return []

        class AllLangsRule(DiagnosticRule):
            @property
            def rule_id(self) -> str:
                return "all_rule"

            @property
            def rule_name(self) -> str:
                return "All Rule"

            def analyze(self, ir):
                return []

        registry = RuleRegistry()
        registry.register(PythonOnlyRule())
        registry.register(AllLangsRule())

        python_rules = registry.get_rules_for_language("python")
        assert len(python_rules) == 2

        go_rules = registry.get_rules_for_language("go")
        assert len(go_rules) == 1


class TestBuiltinRules:
    def test_register_builtin_rules(self):
        registry = RuleRegistry()
        register_builtin_rules(registry)

        assert registry.get_rule("null_dereference") is not None
        assert registry.get_rule("resource_leak") is not None

    def test_null_dereference_rule_metadata(self):
        rule = NullDereferenceRule()
        assert rule.rule_id == "null_dereference"
        assert rule.severity == Severity.ERROR
        assert rule.requires_llm is False

    def test_resource_leak_rule_metadata(self):
        rule = ResourceLeakRule()
        assert rule.rule_id == "resource_leak"
        assert rule.severity == Severity.WARNING
