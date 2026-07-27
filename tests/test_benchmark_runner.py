"""Reproducible snapshot benchmark runner tests."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smartbench.benchmarks import BenchmarkConfigError, BenchmarkRunner, load_benchmark_manifest
from smartbench.cli.main import app
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


RULES = """
version: smartbench.state-rules/v1
rules:
  - id: terminal-before-retry
    name: Terminal state before retry
    severity: error
    languages: [go]
    message: retry requires a terminal-state guard
    invariant:
      kind: require_guard_before_action
      event: {kinds: [branch], contains_all: [ready]}
      guard: {kinds: [branch], contains_all: [completed]}
      action: {kinds: [update], contains_all: [retries]}
""".strip()


BAD = """
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


FIXED = """
package sample
func run(output string) error {
    for {
        if !ready(output) {
            if completed(output) {
                return nil
            }
            retries++
            continue
        }
        return nil
    }
}
""".strip()


def _write_fixture(tmp_path: Path) -> Path:
    (tmp_path / "bad").mkdir()
    (tmp_path / "fixed").mkdir()
    (tmp_path / "bad" / "agent.go").write_text(BAD, encoding="utf-8")
    (tmp_path / "fixed" / "agent.go").write_text(FIXED, encoding="utf-8")
    (tmp_path / "rules.yaml").write_text(RULES, encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """
version: smartbench.benchmarks/v1
cases:
  - id: terminal-state
    language: go
    state_rules: [rules.yaml]
    rules: [terminal-before-retry]
    metadata: {repository: fixture/example, before_commit: abc, after_commit: def}
    snapshots:
      - {label: bad, path: bad, min_findings: 1, max_findings: 1, expected_rule_ids: [terminal-before-retry]}
      - {label: fixed, path: fixed, max_findings: 0}
""".strip(),
        encoding="utf-8",
    )
    return manifest


def test_runner_distinguishes_bad_and_fixed_snapshots(tmp_path: Path):
    manifest = _write_fixture(tmp_path)
    cases = load_benchmark_manifest(manifest)
    report = BenchmarkRunner().run(cases)

    assert report.passed
    assert [(result.label, result.findings, result.passed) for result in report.results] == [
        ("bad", 1, True),
        ("fixed", 0, True),
    ]


def test_runner_rejects_impossible_expectations(tmp_path: Path):
    manifest = _write_fixture(tmp_path)
    text = manifest.read_text(encoding="utf-8").replace(
        "min_findings: 1, max_findings: 1",
        "min_findings: 2, max_findings: 1",
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(BenchmarkConfigError, match="cannot exceed"):
        load_benchmark_manifest(manifest)


def test_benchmark_cli_writes_machine_readable_report(tmp_path: Path):
    manifest = _write_fixture(tmp_path)
    output = tmp_path / "benchmark.json"
    result = CliRunner().invoke(
        app,
        ["benchmark", "run", "--manifest", str(manifest), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == {"total": 2, "passed": 2, "failed": 0}
    assert report["snapshots"][0]["metadata"] == {
        "repository": "fixture/example",
        "before_commit": "abc",
        "after_commit": "def",
    }
