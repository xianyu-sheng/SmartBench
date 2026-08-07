"""Reproducible corpus-level ProjectReader architecture experiment."""

from pathlib import Path

import pytest

from smartbench.experiments.project_reader_resource import (
    run_project_reader_resource_experiment,
)
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


def test_reference_assisted_protocols_detect_go_history_cases():
    root = Path(__file__).resolve().parents[1]
    report = run_project_reader_resource_experiment(
        root / "benchmarks" / "real" / "manifest.yaml",
        negative_path=(
            root / "benchmarks" / "experiments" / "project_reader_resource" / "negative"
        ),
    )

    assert report.passed
    # The corpus grows as new verified cases are added; every Go case must
    # satisfy the assisted-detection contract (baseline may abstain, but the
    # reference-assisted run must find the bug in `before` and not in `after`).
    assert len(report.cases) >= 4
    assert all(case.assisted_before_findings > 0 for case in report.cases)
    assert all(case.assisted_after_findings == 0 for case in report.cases)
    assert report.independent_negative_findings == 0
