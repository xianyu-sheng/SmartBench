"""Regression tests for bounded external-process output."""

import sys

from smartbench.subprocess_utils import run_bounded


def test_run_bounded_uses_stable_head_tail_marker():
    """Issue #19 / qscan #22: keep omitted-byte report output golden-stable."""
    result = run_bounded(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('0123456789ABCDEFGHIJ')",
        ],
        max_stdout_bytes=10,
    )

    assert result.returncode == 0
    assert result.stdout == (
        "0123456\n... [10 output bytes omitted] ...\nHIJ"
    )
