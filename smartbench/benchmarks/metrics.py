"""Corpus-level metrics: precision, recall, false-positive rate and latency.

Metrics are **case-level** and scoped to each case's declared target rules
(``case.rule_ids`` plus any rule an expected snapshot names). Findings outside
those rules are ignored, which is stated in the report itself. The counters are
deterministic; only the latency block is a measurement and may vary between
runs of the same machine.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from smartbench.benchmarks.runner import (
    BenchmarkCase,
    BenchmarkReport,
    BenchmarkSnapshot,
    BenchmarkSnapshotResult,
)

METRICS_SCHEMA_VERSION = "smartbench.metrics/v1"

_COUNTER_KEYS = ("tp", "fp", "fn", "tn")


@dataclass(frozen=True)
class BenchmarkRun:
    """One snapshot paired with the case that declares its expectations."""

    case: BenchmarkCase
    snapshot: BenchmarkSnapshot
    result: BenchmarkSnapshotResult


def collect_runs(
    cases: Sequence[BenchmarkCase],
    report: BenchmarkReport,
) -> list[BenchmarkRun]:
    """Pair manifest cases with runner results, preserving manifest order."""
    runs: list[BenchmarkRun] = []
    index = 0
    for case in cases:
        for snapshot in case.snapshots:
            if index >= len(report.results):
                raise ValueError("benchmark report has fewer snapshots than the manifest")
            runs.append(BenchmarkRun(case, snapshot, report.results[index]))
            index += 1
    if index != len(report.results):
        raise ValueError("benchmark report has more snapshots than the manifest")
    return runs


def compute_metrics(
    positive: Sequence[BenchmarkRun],
    negative: Sequence[BenchmarkRun] = (),
) -> dict[str, object]:
    """Aggregate case-level metrics over positive and negative corpora."""
    runs = list(positive) + list(negative)
    overall = _empty_counts()
    by_rule: dict[str, dict[str, int]] = defaultdict(_empty_counts)
    by_language: dict[str, dict[str, int]] = defaultdict(_empty_counts)
    by_category: dict[str, dict[str, int]] = defaultdict(_empty_counts)
    latency: list[int] = []
    errors = 0

    for run in runs:
        case = run.case
        snapshot = run.snapshot
        result = run.result
        target_rules = set(case.rule_ids) | set(snapshot.expected_rule_ids)
        actual_rules = set(result.finding_rule_ids)
        expected_rules = set(snapshot.expected_rule_ids)
        category = _category(case)

        for rule in sorted(target_rules):
            expected = rule in expected_rules
            actual = rule in actual_rules
            for bucket in (overall, by_rule[rule], by_language[case.language], by_category[category]):
                _record(bucket, expected, actual)

        latency.append(result.duration_ms)
        if result.errors:
            errors += 1

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "scope": (
            "case-level; target rules come from case.rules and expected_rule_ids; "
            "findings outside the target rules are ignored"
        ),
        "corpora": {
            "positive": _corpus_summary(positive),
            "negative": _corpus_summary(negative),
        },
        "overall": _summarize(overall, latency=latency, errors=errors),
        "by_rule": {
            rule: _summarize(bucket) for rule, bucket in sorted(by_rule.items())
        },
        "by_language": {
            language: _summarize(bucket)
            for language, bucket in sorted(by_language.items())
        },
        "by_category": {
            category: _summarize(bucket)
            for category, bucket in sorted(by_category.items())
        },
        "abstention_rate": None,
        "abstention_note": (
            "deterministic rules have no abstention state; LLM-path abstentions "
            "are measured by quick runs and are not part of this corpus"
        ),
        "llm": {
            "ran": False,
            "tokens": None,
            "cost_usd": None,
            "note": "opt-in; the deterministic corpus does not call a provider",
        },
    }


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in _COUNTER_KEYS}


def _record(bucket: dict[str, int], expected: bool, actual: bool) -> None:
    if expected and actual:
        bucket["tp"] += 1
    elif expected and not actual:
        bucket["fn"] += 1
    elif not expected and actual:
        bucket["fp"] += 1
    else:
        bucket["tn"] += 1


def _category(case: BenchmarkCase) -> str:
    metadata = case.metadata or {}
    return str(
        metadata.get("bug_category")
        or metadata.get("category")
        or "uncategorized"
    )


def _corpus_summary(runs: Sequence[BenchmarkRun]) -> dict[str, int]:
    return {
        "cases": len({run.case.case_id for run in runs}),
        "snapshots": len(runs),
    }


def _summarize(
    counts: dict[str, int],
    *,
    latency: Sequence[int] | None = None,
    errors: int | None = None,
) -> dict[str, object]:
    tp, fp, fn, tn = (counts[key] for key in _COUNTER_KEYS)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = _round(2 * precision * recall / (precision + recall))
    summary: dict[str, object] = {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_rate": _ratio(fp, fp + tn),
    }
    if latency is not None:
        ordered = sorted(latency)
        summary["snapshots"] = len(ordered)
        summary["latency_ms"] = {
            "p50": _percentile(ordered, 50),
            "p95": _percentile(ordered, 95),
            "max": ordered[-1] if ordered else None,
        }
    if errors is not None:
        summary["snapshots_with_errors"] = errors
    return summary


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return _round(numerator / denominator)


def _round(value: float) -> float:
    return round(value, 4)


def _percentile(ordered: Sequence[int], percentile: int) -> int | None:
    if not ordered:
        return None
    rank = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return ordered[min(rank, len(ordered) - 1)]
