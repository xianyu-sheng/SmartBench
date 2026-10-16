"""Counterfactual retry-loop analysis tests (engineering trade-off signal)."""

from pathlib import Path

import pytest

from smartbench.benchmarks import BenchmarkRunner, load_benchmark_manifest
from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
)
from smartbench.core.adapters import GoAdapter
from smartbench.core.rules.retry import RetryWithoutBackoffRule
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")

RULE_ID = "retry_without_backoff"

BEFORE = """
package sample

import (
	"log"
	"net"
)

func listen(l net.Listener) {
	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accepting connection: %s", err)
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
"""

BUSY_LOOP = """
package sample

import (
	"log"
	"net"
)

func listen(l net.Listener) {
	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accepting connection: %s", err)
			continue
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
"""

WITH_BACKOFF = """
package sample

import (
	"log"
	"net"
	"time"
)

func listen(l net.Listener) {
	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accepting connection: %s", err)
			time.Sleep(time.Second)
			continue
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
"""

BOUNDED = """
package sample

import (
	"log"
	"net"
)

func listen(l net.Listener) {
	attempts := 0
	for {
		c, err := l.Accept()
		if err != nil {
			attempts++
			if attempts > 3 {
				return
			}
			continue
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
"""

CHANNEL_LOOP = """
package sample

func consume(ch chan int) {
	for {
		value, ok := <-ch
		if !ok {
			return
		}
		_ = value
	}
}
"""

PROGRESS_GUARDED = """
package sample

import (
	"os"
	"path/filepath"
)

func find(path string) (string, error) {
	for {
		_, err := os.Stat(path)
		if err == nil {
			return path, nil
		}
		if dir := filepath.Dir(path); dir != path {
			path = dir
			continue
		}
		return "", err
	}
}
"""


def _line_of(source: str, needle: str) -> int:
    for index, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return index
    raise AssertionError(f"missing line: {needle}")


def _run_rule(tmp_path: Path, source: str):
    (tmp_path / "main.go").write_text(source, encoding="utf-8")
    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    rules = RuleRegistry()
    rules.register(RetryWithoutBackoffRule())
    engine = UnifiedDiagnosticEngine(adapters, rules)
    return engine.diagnose(
        tmp_path,
        UnifiedDiagnosticConfig(
            rule_ids=[RULE_ID],
            min_confidence=0.0,
            build_evidence_packs=False,
        ),
    )


def test_immediate_retry_without_backoff_is_reported(tmp_path: Path):
    result = _run_rule(tmp_path, BUSY_LOOP)

    assert result.errors == []
    assert [finding.rule_id for finding in result.findings] == [RULE_ID]
    finding = result.findings[0]
    assert "l.Accept" in BUSY_LOOP.splitlines()[finding.location.line_start - 1]
    assert finding.metadata["patch_risk"] == "regression_under_persistent_error"
    assert finding.metadata["mitigation"] == "none"
    assert finding.metadata["trigger_operation"] == "l.Accept"
    assert {reference.line_start for reference in finding.evidence} == {
        _line_of(BUSY_LOOP, "for {"),
        _line_of(BUSY_LOOP, "if err != nil"),
        _line_of(BUSY_LOOP, "continue"),
    }


def test_fallthrough_error_path_is_not_a_retry(tmp_path: Path):
    assert _run_rule(tmp_path, BEFORE).findings == []


def test_backoff_mitigates_the_retry(tmp_path: Path):
    assert _run_rule(tmp_path, WITH_BACKOFF).findings == []


def test_bounded_retry_is_not_reported(tmp_path: Path):
    assert _run_rule(tmp_path, BOUNDED).findings == []


def test_non_retry_loop_is_not_reported(tmp_path: Path):
    assert _run_rule(tmp_path, CHANNEL_LOOP).findings == []


def test_progress_guarded_walk_is_not_reported(tmp_path: Path):
    assert _run_rule(tmp_path, PROGRESS_GUARDED).findings == []


def test_rule_declares_semantic_control_flow_contract():
    rule = RetryWithoutBackoffRule()

    assert rule.rule_id == RULE_ID
    assert rule.analysis_method.value == "semantic"
    assert rule.required_capabilities == {"control_flow"}


def test_tradeoff_benchmark_manifest_passes():
    manifest = (
        Path(__file__).resolve().parents[1] / "benchmarks" / "tradeoffs" / "manifest.yaml"
    )
    cases = load_benchmark_manifest(manifest)
    report = BenchmarkRunner().run(cases)

    assert report.passed, [result.to_dict() for result in report.results if not result.passed]
    assert report.to_dict()["summary"] == {"total": 3, "passed": 3, "failed": 0}
