"""Tests for corpus-level metrics (issue #10)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from smartbench.benchmarks.metrics import BenchmarkRun, compute_metrics
from smartbench.benchmarks.runner import (
    BenchmarkCase,
    BenchmarkSnapshot,
    BenchmarkSnapshotResult,
)
from smartbench.cli.main import app


def _run(
    case_id: str,
    rule: str,
    expected: list[str],
    actual: list[str],
    *,
    duration_ms: int = 10,
    label: str = "clean",
):
    snapshot = BenchmarkSnapshot(label, Path("."), 0, 0, frozenset(expected))
    case = BenchmarkCase(
        case_id,
        "python",
        (),
        (rule,),
        (snapshot,),
        {"bug_category": "resource-lifecycle"},
    )
    result = BenchmarkSnapshotResult(
        case_id=case_id,
        label=label,
        path=".",
        findings=len(actual),
        finding_rule_ids=list(actual),
        metadata={},
        errors=[],
        passed=not actual,
        duration_ms=duration_ms,
    )
    return BenchmarkRun(case, snapshot, result)


def test_counts_and_ratios():
    runs = [
        _run("tp", "resource_leak", ["resource_leak"], ["resource_leak"]),
        _run("fn", "resource_leak", ["resource_leak"], []),
        _run("fp", "resource_leak", [], ["resource_leak"]),
        _run("tn", "resource_leak", [], []),
    ]
    metrics = compute_metrics(runs)
    overall = metrics["overall"]

    assert (overall["tp"], overall["fp"], overall["fn"], overall["tn"]) == (1, 1, 1, 1)
    assert overall["precision"] == 0.5
    assert overall["recall"] == 0.5
    assert overall["f1"] == 0.5
    assert overall["false_positive_rate"] == 0.5
    assert metrics["by_rule"]["resource_leak"]["fp"] == 1


def test_latency_percentiles():
    runs = [
        _run("a", "resource_leak", ["resource_leak"], ["resource_leak"], duration_ms=4),
        _run("b", "resource_leak", ["resource_leak"], ["resource_leak"], duration_ms=6),
        _run("c", "resource_leak", ["resource_leak"], ["resource_leak"], duration_ms=8),
        _run("d", "resource_leak", ["resource_leak"], ["resource_leak"], duration_ms=100),
    ]
    latency = compute_metrics(runs)["overall"]["latency_ms"]

    assert latency == {"p50": 6, "p95": 100, "max": 100}


def test_output_is_deterministic():
    runs = [
        _run("tp", "resource_leak", ["resource_leak"], ["resource_leak"]),
        _run("tn", "resource_leak", [], []),
    ]
    assert compute_metrics(runs) == compute_metrics(runs)


def test_metrics_cli_writes_json(tmp_path):
    (tmp_path / "before").mkdir()
    (tmp_path / "before" / "main.py").write_text(
        "def read(path):\n"
        "    handle = open(path)\n"
        "    return handle.read()\n",
        encoding="utf-8",
    )
    (tmp_path / "after").mkdir()
    (tmp_path / "after" / "main.py").write_text(
        "def read(path):\n"
        "    with open(path) as handle:\n"
        "        return handle.read()\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "version: smartbench.benchmarks/v1\n"
        "cases:\n"
        "  - id: cleanup\n"
        "    language: python\n"
        "    rules: [resource_leak]\n"
        "    metadata:\n"
        "      bug_category: resource-lifecycle\n"
        "    snapshots:\n"
        "      - label: before\n"
        "        path: before\n"
        "        min_findings: 1\n"
        "        expected_rule_ids: [resource_leak]\n"
        "      - label: after\n"
        "        path: after\n"
        "        max_findings: 0\n",
        encoding="utf-8",
    )
    destination = tmp_path / "metrics.json"

    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "metrics",
            "--manifest",
            str(manifest),
            "--json",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0, result.output
    metrics = json.loads(destination.read_text(encoding="utf-8"))
    assert metrics["schema_version"] == "smartbench.metrics/v1"
    assert metrics["overall"]["precision"] == 1.0
    assert metrics["overall"]["recall"] == 1.0
    assert metrics["overall"]["false_positive_rate"] == 0.0
    assert metrics["overall"]["tp"] == 1
    assert metrics["overall"]["tn"] == 1
    assert metrics["abstention_rate"] is None
    assert metrics["llm"]["ran"] is False
