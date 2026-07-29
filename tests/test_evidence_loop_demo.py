"""The portfolio demo must remain fully reproducible without an API key."""

from pathlib import Path

import pytest

from smartbench.experiments.evidence_loop_demo import run_demo
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


def test_offline_demo_exposes_gate_repair_resolver_and_witness():
    root = Path(__file__).resolve().parents[1]
    report = run_demo(root)
    trials = [trial for case in report.cases for trial in case.trials]

    assert report.passed
    assert len(trials) == 2
    assert all(trial.initial_rejected_candidates == 1 for trial in trials)
    assert all(trial.repair_attempts == 1 for trial in trials)
    assert all(trial.recovered_by_repair for trial in trials)
    assert all(trial.resolved_candidates == 1 for trial in trials)
    assert all(trial.before_findings == 1 and trial.after_findings == 0 for trial in trials)
    assert all(trial.negative_findings == 0 for trial in trials)
    assert all(trial.finding_witnesses for trial in trials)
