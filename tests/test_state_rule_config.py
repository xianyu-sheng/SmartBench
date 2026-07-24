"""Declarative state-rule loading and unified-engine integration."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smartbench.analysis import StateRuleConfigError, load_state_rule_file
from smartbench.cli.main import app
from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
)
from smartbench.core.adapters import GoAdapter
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


RULE_DOCUMENT = """
version: smartbench.state-rules/v1
rules:
  - id: terminal-before-retry
    name: Terminal state before retry
    description: A completed event must be handled before retrying.
    severity: error
    confidence: 0.95
    languages: [go]
    message: retry requires a terminal-state guard
    invariant:
      kind: require_guard_before_action
      event:
        kinds: [branch]
        contains_all: [ready]
      guard:
        kinds: [branch]
        contains_all: [completed]
      action:
        kinds: [update]
        contains_all: [retries]
""".strip()


SOURCE = """
package sample

func run(output string) error {
    for {
        if !ready(output) {
            retries++
            continue
        }
        return nil
    }
}
""".strip()


def test_state_rule_loader_validates_versioned_document(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(RULE_DOCUMENT, encoding="utf-8")

    definitions = load_state_rule_file(path)

    assert [definition.rule_id for definition in definitions] == [
        "terminal-before-retry"
    ]
    assert definitions[0].languages == frozenset({"go"})
    assert definitions[0].confidence == 0.95


def test_state_rule_loader_rejects_unknown_fields(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        RULE_DOCUMENT.replace("    name:", "    unexpected: true\n    name:"),
        encoding="utf-8",
    )

    with pytest.raises(StateRuleConfigError, match="unknown fields"):
        load_state_rule_file(path)


def test_unified_engine_emits_finding_and_exact_evidence_pack(tmp_path: Path):
    source_path = tmp_path / "agent.go"
    rule_path = tmp_path / "rules.yaml"
    source_path.write_text(SOURCE, encoding="utf-8")
    rule_path.write_text(RULE_DOCUMENT, encoding="utf-8")

    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())
    result = engine.diagnose_file(
        source_path,
        tmp_path,
        UnifiedDiagnosticConfig(
            use_static_rules=False,
            state_rule_paths=[rule_path],
        ),
    )

    assert result.errors == []
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "terminal-before-retry"
    assert finding.location.file_path == "agent.go"
    assert finding.location.line_start == 6
    assert finding.confidence == 0.95

    pack = result.evidence_packs[finding.metadata["evidence_pack_id"]]
    violation_fact = next(
        fact for fact in pack.facts
        if fact.attributes.get("rule_id") == "terminal-before-retry"
    )
    assert violation_fact.fact_id.startswith("fact-")
    assert {reference.line_start for reference in violation_fact.evidence} == {5, 6}
    assert all(reference.source == "go_frontend" for reference in violation_fact.evidence)


def test_cli_loads_repeatable_state_rule_option(tmp_path: Path):
    source_path = tmp_path / "agent.go"
    rule_path = tmp_path / "rules.yaml"
    report_path = tmp_path / "report.json"
    source_path.write_text(SOURCE, encoding="utf-8")
    rule_path.write_text(RULE_DOCUMENT, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "unified",
            "run",
            "--project",
            str(tmp_path),
            "--language",
            "go",
            "--rule",
            "terminal-before-retry",
            "--state-rules",
            str(rule_path),
            "--output",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [finding["rule_id"] for finding in report["findings"]] == [
        "terminal-before-retry"
    ]
    assert len(report["evidence_packs"]) == 1
