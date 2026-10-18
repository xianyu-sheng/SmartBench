"""Unit tests for the CLI progress reporter (issue #21).

The reporter is presentation-only: these tests pin the single-line status
format, the TTY/``--json`` degradation rules, and the guarantee that a
misbehaving reporter can never break a run.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from smartbench.cli.progress import (
    DISABLE_ENV_VAR,
    LiveProgressReporter,
    NullProgressReporter,
    ProgressEvent,
    ProgressReporter,
    build_progress_reporter,
    format_status,
    format_tokens,
    phase_label,
)


class FakeClock:
    """Deterministic monotonic clock for elapsed-time assertions."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_tty_console() -> tuple[Console, io.StringIO]:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, width=120, legacy_windows=False)
    return console, stream


def make_plain_console() -> tuple[Console, io.StringIO]:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=120)
    return console, stream


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (0, "0"),
        (7, "7"),
        (999, "999"),
        (1000, "1k"),
        (1500, "1.5k"),
        (12345, "12.3k"),
        (1_000_000, "1M"),
        (2_400_000, "2.4M"),
        (-5, "0"),
        ("nonsense", "0"),
    ],
)
def test_format_tokens_is_compact(tokens, expected):
    assert format_tokens(tokens) == expected


def test_phase_label_maps_known_phases_and_passes_through_unknown():
    assert phase_label("proposer") == "Proposer"
    assert phase_label("judge") == "Judge"
    assert phase_label("custom_phase") == "custom_phase"
    assert phase_label("") == "Working"


def test_format_status_is_a_single_line_with_round_tokens_and_elapsed():
    event = ProgressEvent(
        phase="critique",
        round_index=2,
        round_total=3,
        tokens_used=1500,
        elapsed_s=12.34,
    )
    line = format_status(event)
    assert line == "Critique · round 2/3 · 1.5k tokens · 12.3s"
    assert "\n" not in line


def test_format_status_omits_round_when_total_unknown_and_flattens_detail():
    event = ProgressEvent(
        phase="project_reader",
        tokens_used=0,
        elapsed_s=0.0,
        detail="scanning\nsrc/app.py",
    )
    line = format_status(event)
    assert line == "ProjectReader · 0 tokens · 0.0s · scanning src/app.py"
    assert "\n" not in line


# ---------------------------------------------------------------------------
# NullProgressReporter / base bookkeeping
# ---------------------------------------------------------------------------


def test_null_reporter_is_silent_but_records_events():
    clock = FakeClock()
    reporter = NullProgressReporter(clock=clock)
    assert reporter.enabled is False

    clock.advance(2.5)
    reporter.report("proposer", round_index=1, round_total=3, tokens_used=0)
    clock.advance(1.0)
    reporter.report("critique", round_index=2, round_total=3, tokens_used=1500)

    assert [event.phase for event in reporter.events] == ["proposer", "critique"]
    assert [event.round_index for event in reporter.events] == [1, 2]
    assert reporter.events[-1].tokens_used == 1500
    assert reporter.state.phase == "critique"
    # elapsed is measured from the first report
    assert reporter.events[0].elapsed_s == pytest.approx(0.0)
    assert reporter.events[1].elapsed_s == pytest.approx(1.0)


def test_reporter_normalizes_negative_and_non_numeric_values():
    reporter = NullProgressReporter(clock=FakeClock())
    event = reporter.report("judge", round_index=-2, round_total=-3, tokens_used=-100)
    assert event.round_index == 0
    assert event.round_total == 0
    assert event.tokens_used == 0


def test_base_reporter_context_manager_finishes_and_closes():
    reporter = ProgressReporter(clock=FakeClock())
    with reporter as active:
        assert active is reporter
        active.report("proposer", round_index=1, round_total=3)
    assert [event.phase for event in reporter.events] == ["proposer", "done"]
    # close/finish are idempotent
    reporter.close()
    reporter.finish()


def test_finish_on_unused_reporter_is_a_noop():
    reporter = NullProgressReporter(clock=FakeClock())
    reporter.finish()
    assert reporter.events == []


# ---------------------------------------------------------------------------
# LiveProgressReporter
# ---------------------------------------------------------------------------


def test_live_reporter_renders_status_rows_and_closes_idempotently():
    console, stream = make_tty_console()
    reporter = LiveProgressReporter(console, refresh_per_second=1000)
    assert reporter.enabled is True

    reporter.report("proposer", round_index=1, round_total=3)
    reporter.report("critique", round_index=2, round_total=3, tokens_used=1500)
    reporter.close()
    reporter.close()

    output = stream.getvalue()
    # ``Live`` re-renders in place, so only the newest status row survives.
    assert "Critique · round 2/3 · 1.5k tokens" in output
    assert "round 2/3" in output
    # every phase still flowed through the reporter callback
    assert [event.label for event in reporter.events] == ["Proposer", "Critique"]
    assert reporter._live is None


def test_live_reporter_finish_emits_done_and_stops_rendering():
    console, stream = make_tty_console()
    reporter = LiveProgressReporter(console, refresh_per_second=1000)
    reporter.report("proposer", round_index=1, round_total=3)
    reporter.finish()

    assert reporter.state.phase == "done"
    assert "Done" in stream.getvalue()
    assert reporter._live is None


# ---------------------------------------------------------------------------
# build_progress_reporter degradation rules
# ---------------------------------------------------------------------------


def test_build_returns_live_reporter_for_interactive_terminal():
    console, _ = make_tty_console()
    reporter = build_progress_reporter(console)
    try:
        assert isinstance(reporter, LiveProgressReporter)
        assert reporter.enabled is True
    finally:
        reporter.close()


def test_build_returns_null_reporter_for_captured_output():
    console, _ = make_plain_console()
    assert isinstance(build_progress_reporter(console), NullProgressReporter)


def test_json_mode_never_writes_progress_noise_to_the_output_stream():
    console, stream = make_tty_console()
    reporter = build_progress_reporter(console, json_mode=True)
    assert isinstance(reporter, NullProgressReporter)

    reporter.report("proposer", round_index=1, round_total=3, tokens_used=10)
    reporter.finish()

    assert stream.getvalue() == ""
    # events are still recorded, so callers/tests can inspect them
    assert reporter.state.phase == "done"


def test_disable_env_var_forces_silent_reporter(monkeypatch):
    console, _ = make_tty_console()
    monkeypatch.setenv(DISABLE_ENV_VAR, "1")
    assert isinstance(build_progress_reporter(console), NullProgressReporter)


def test_build_accepts_none_console_without_crashing():
    reporter = build_progress_reporter(None)
    assert isinstance(reporter, ProgressReporter)
    reporter.close()
