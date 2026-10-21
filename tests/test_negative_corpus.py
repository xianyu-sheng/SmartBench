"""Negative-corpus regression: clean look-alike code must stay finding-free."""

from pathlib import Path

from smartbench.benchmarks.runner import BenchmarkRunner, load_benchmark_manifest

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "benchmarks"
    / "negative"
    / "manifest.yaml"
)


def test_negative_corpus_is_false_positive_free():
    report = BenchmarkRunner().run(load_benchmark_manifest(MANIFEST))
    failures = [
        (result.case_id, result.label, result.finding_rule_ids)
        for result in report.results
        if not result.passed
    ]
    assert not failures, failures


def test_negative_corpus_covers_the_required_shapes():
    cases = load_benchmark_manifest(MANIFEST)
    assert len(cases) >= 10
    assert len({case.metadata.get("category") for case in cases}) >= 4
    for case in cases:
        for snapshot in case.snapshots:
            assert snapshot.max_findings == 0
            assert snapshot.min_confidence == 0.7
