"""Blind ProjectReader protocol transfer over excluded target files."""

from pathlib import Path

import pytest

from smartbench.experiments.project_reader_blind import (
    run_blind_project_reader_experiment,
)
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


def test_blind_cross_file_protocol_transfer_is_partial_and_auditable():
    root = Path(__file__).resolve().parents[1]
    report = run_blind_project_reader_experiment(
        root / "benchmarks" / "real" / "manifest.yaml",
        root / "benchmarks" / "experiments" / "project_reader_blind" / "manifest.yaml",
        negative_path=(
            root / "benchmarks" / "experiments" / "project_reader_resource" / "negative"
        ),
    )
    encoded = report.to_dict()

    assert report.passed
    assert encoded["decision"] == "partial"
    assert encoded["summary"] == {
        "cases": 4,
        "reference_available": 2,
        "exact_before_detected": 1,
        "shape_before_detected": 2,
        "shape_after_clean": 2,
        "diagnostic_coverage": 0.5,
    }
    assert report.independent_negative_findings == 0
    assert all(case.target_paths_excluded for case in report.cases)
    assert all(case.source_hashes_verified for case in report.cases)

    detected = [case for case in report.cases if case.shape_before_findings]
    assert len(detected) == 2
    assert all(case.shape_after_findings == 0 for case in detected)
    assert all(all(case.verification.values()) for case in detected)

    unsupported = [case for case in report.cases if not case.reference_available]
    assert len(unsupported) == 2
    assert all(case.unsupported_reason for case in unsupported)
    assert all(case.shape_before_findings == 0 for case in unsupported)
