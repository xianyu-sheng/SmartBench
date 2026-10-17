"""Regression tests for issue #27 item 3: `check-branches --fail-on` gating."""

from __future__ import annotations

from pathlib import Path

import pytest

from smartbench.cli.main import (
    _count_unresolved_at_or_above,
    _enforce_branch_gate,
    app,
)
from smartbench.frontends.git_branch_checker import BranchCheckResult


def _results() -> list[BranchCheckResult]:
    return [
        BranchCheckResult(file_path="a.go", line_start=3, line_end=3, severity="warning"),
        BranchCheckResult(
            file_path="b.go",
            line_start=9,
            line_end=9,
            severity="error",
            already_fixed_on=["dev"],
        ),
        BranchCheckResult(file_path="c.go", line_start=12, line_end=12, severity="info"),
        BranchCheckResult(
            file_path="d.go",
            line_start=1,
            line_end=1,
            severity="error",
            error="boom",
        ),
    ]


def test_gate_counts_only_unresolved_findings():
    results = _results()

    assert _count_unresolved_at_or_above(results, "none") == 0
    assert _count_unresolved_at_or_above(results, "info") == 2
    assert _count_unresolved_at_or_above(results, "warning") == 1
    # The only `error` finding is already fixed, so `error` gating passes.
    assert _count_unresolved_at_or_above(results, "error") == 0


def test_gate_exits_one_when_tripped():
    import typer

    _enforce_branch_gate(0, "warning")
    with pytest.raises(typer.Exit) as excinfo:
        _enforce_branch_gate(1, "warning")
    assert excinfo.value.exit_code == 1


def test_invalid_fail_on_value_exits_two(tmp_path: Path):
    from typer.testing import CliRunner

    payload = tmp_path / "report.json"
    payload.write_text('{"final_suggestions": []}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "check-branches",
            "--input",
            str(payload),
            "--repo",
            str(tmp_path),
            "--fail-on",
            "bogus",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid --fail-on value" in result.output


def test_branch_result_serialises_severity():
    result = BranchCheckResult(file_path="a.go", line_start=1, line_end=2, severity="error")

    assert result.to_dict()["severity"] == "error"
