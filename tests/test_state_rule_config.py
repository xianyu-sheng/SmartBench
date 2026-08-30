"""Declarative state-rule loading and unified-engine integration."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smartbench.analysis import StateRuleConfigError, StateScope, load_state_rule_file
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

    assert [definition.rule_id for definition in definitions] == ["terminal-before-retry"]
    assert definitions[0].languages == frozenset({"go"})
    assert definitions[0].confidence == 0.95
    assert definitions[0].invariant.scope == StateScope.INTRAPROCEDURAL


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
        fact for fact in pack.facts if fact.attributes.get("rule_id") == "terminal-before-retry"
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
    assert [finding["rule_id"] for finding in report["findings"]] == ["terminal-before-retry"]
    assert len(report["evidence_packs"]) == 1


# ---------------------------------------------------------------------------
# YAML round-trip tests for forbid_action_after_event
# ---------------------------------------------------------------------------

FORBID_RULE_DOCUMENT = """
version: smartbench.state-rules/v1
rules:
  - id: no-commit-after-rollback
    name: No commit after rollback
    description: Commit must not be called after Rollback on the same transaction.
    severity: error
    confidence: 1.0
    languages: [go]
    message: Commit must not be called after Rollback
    invariant:
      kind: forbid_action_after_event
      event:
        kinds: [call]
        contains_all: [Rollback]
      action:
        kinds: [call]
        contains_all: [Commit]
""".strip()

FORBID_SOURCE_BEFORE = """
package sample

import "database/sql"

func finish(tx *sql.Tx, ok bool) error {
    if !ok {
        tx.Rollback()
        tx.Commit()
    }
    return nil
}
""".strip()

FORBID_SOURCE_AFTER = """
package sample

import "database/sql"

func finish(tx *sql.Tx, ok bool) error {
    if !ok {
        return tx.Rollback()
    }
    return tx.Commit()
}
""".strip()


def test_forbid_rule_loader_parses_yaml(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(FORBID_RULE_DOCUMENT, encoding="utf-8")

    definitions = load_state_rule_file(path)

    assert len(definitions) == 1
    defn = definitions[0]
    assert defn.rule_id == "no-commit-after-rollback"
    from smartbench.analysis.state_machine import InvariantKind
    assert defn.invariant.kind == InvariantKind.FORBID_ACTION_AFTER_EVENT
    assert defn.invariant.guard is None


def test_forbid_rule_end_to_end_before(tmp_path: Path):
    (tmp_path / "main.go").write_text(FORBID_SOURCE_BEFORE, encoding="utf-8")
    rule_path = tmp_path / "rules.yaml"
    rule_path.write_text(FORBID_RULE_DOCUMENT, encoding="utf-8")

    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())
    result = engine.diagnose_file(
        tmp_path / "main.go",
        tmp_path,
        UnifiedDiagnosticConfig(use_static_rules=False, state_rule_paths=[rule_path]),
    )

    assert result.errors == []
    assert len(result.findings) >= 1
    assert result.findings[0].rule_id == "no-commit-after-rollback"


def test_forbid_rule_end_to_end_after(tmp_path: Path):
    (tmp_path / "main.go").write_text(FORBID_SOURCE_AFTER, encoding="utf-8")
    rule_path = tmp_path / "rules.yaml"
    rule_path.write_text(FORBID_RULE_DOCUMENT, encoding="utf-8")

    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())
    result = engine.diagnose_file(
        tmp_path / "main.go",
        tmp_path,
        UnifiedDiagnosticConfig(use_static_rules=False, state_rule_paths=[rule_path]),
    )

    assert result.errors == []
    assert result.findings == []


# ---------------------------------------------------------------------------
# YAML round-trip tests for require_exit_after_event
# ---------------------------------------------------------------------------

REQUIRE_EXIT_RULE_DOCUMENT = """
version: smartbench.state-rules/v1
rules:
  - id: return-after-http-error
    name: Return after http.Error
    description: Handler must return after writing an error response.
    severity: error
    confidence: 1.0
    languages: [go]
    message: handler must return after writing an error response
    invariant:
      kind: require_exit_after_event
      event:
        kinds: [call]
        contains_all: [http.Error]
      action:
        kinds: [call]
        contains_all: [writeJSON]
""".strip()

REQUIRE_EXIT_SOURCE_BEFORE = """
package sample

import "net/http"

func handle(w http.ResponseWriter, r *http.Request) {
    if r.Method != "GET" {
        http.Error(w, "not allowed", http.StatusMethodNotAllowed)
    }
    writeJSON(w, "ok")
}

func writeJSON(w http.ResponseWriter, v any) {}
""".strip()

REQUIRE_EXIT_SOURCE_AFTER = """
package sample

import "net/http"

func handle(w http.ResponseWriter, r *http.Request) {
    if r.Method != "GET" {
        http.Error(w, "not allowed", http.StatusMethodNotAllowed)
        return
    }
    writeJSON(w, "ok")
}

func writeJSON(w http.ResponseWriter, v any) {}
""".strip()


def test_require_exit_rule_loader_parses_yaml(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(REQUIRE_EXIT_RULE_DOCUMENT, encoding="utf-8")

    definitions = load_state_rule_file(path)

    assert len(definitions) == 1
    defn = definitions[0]
    assert defn.rule_id == "return-after-http-error"
    from smartbench.analysis.state_machine import InvariantKind
    assert defn.invariant.kind == InvariantKind.REQUIRE_EXIT_AFTER_EVENT
    assert defn.invariant.guard is None


def test_require_exit_rule_end_to_end_before(tmp_path: Path):
    (tmp_path / "main.go").write_text(REQUIRE_EXIT_SOURCE_BEFORE, encoding="utf-8")
    rule_path = tmp_path / "rules.yaml"
    rule_path.write_text(REQUIRE_EXIT_RULE_DOCUMENT, encoding="utf-8")

    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())
    result = engine.diagnose_file(
        tmp_path / "main.go",
        tmp_path,
        UnifiedDiagnosticConfig(use_static_rules=False, state_rule_paths=[rule_path]),
    )

    assert result.errors == []
    assert len(result.findings) >= 1
    assert result.findings[0].rule_id == "return-after-http-error"


def test_require_exit_rule_end_to_end_after(tmp_path: Path):
    (tmp_path / "main.go").write_text(REQUIRE_EXIT_SOURCE_AFTER, encoding="utf-8")
    rule_path = tmp_path / "rules.yaml"
    rule_path.write_text(REQUIRE_EXIT_RULE_DOCUMENT, encoding="utf-8")

    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())
    result = engine.diagnose_file(
        tmp_path / "main.go",
        tmp_path,
        UnifiedDiagnosticConfig(use_static_rules=False, state_rule_paths=[rule_path]),
    )

    assert result.errors == []
    assert result.findings == []


def test_forbid_rule_rejects_unknown_invariant_kind(tmp_path: Path):
    bad_doc = FORBID_RULE_DOCUMENT.replace(
        "kind: forbid_action_after_event", "kind: unknown_kind"
    )
    path = tmp_path / "rules.yaml"
    path.write_text(bad_doc, encoding="utf-8")

    with pytest.raises(StateRuleConfigError, match="kind must be one of"):
        load_state_rule_file(path)
