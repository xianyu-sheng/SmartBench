"""Single-line progress reporting for long-running CLI tasks (issue #21).

A ``quick``/``diagnose`` run used to be silent between the project scan and
the final report, even though the debate loop already knows exactly which
phase it is in.  This module provides **one** callback interface
(:class:`ProgressReporter`) that the engine reports phase transitions into,
plus:

* :class:`LiveProgressReporter` — a single-line ``rich.Live`` status row for
  interactive terminals (phase, round ``x/y``, tokens, elapsed);
* :class:`NullProgressReporter` — a silent reporter used for ``--json`` runs
  and for non-TTY (captured/CI) output, so machine-readable output stays
  free of progress noise.

Contract:

* presentation only — reporters never change analysis results;
* a reporter that raises must never abort a run (the engine swallows);
* ``--json`` mode always degrades to the silent reporter.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from rich.console import Console
from rich.live import Live
from rich.text import Text

#: Env var that force-disables the live status row (used by scripts/CI).
DISABLE_ENV_VAR = "SMARTBENCH_NO_PROGRESS"

#: Human-readable labels for the phases the debate loop reports.
PHASE_LABELS = {
    "project_reader": "ProjectReader",
    "proposer": "Proposer",
    "verifier": "Verifier",
    "critique": "Critique",
    "judge": "Judge",
    "done": "Done",
}


def format_tokens(tokens: int) -> str:
    """Format a token estimate compactly (``1500`` -> ``"1.5k"``)."""
    try:
        value = int(tokens)
    except (TypeError, ValueError):
        return "0"
    if value < 0:
        value = 0
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M")


def phase_label(phase: str) -> str:
    """Return a display label for ``phase`` (unknown phases pass through)."""
    return PHASE_LABELS.get(phase, phase or "Working")


@dataclass(frozen=True)
class ProgressEvent:
    """One immutable snapshot of what the CLI should show."""

    phase: str
    round_index: int = 0
    round_total: int = 0
    tokens_used: int = 0
    elapsed_s: float = 0.0
    detail: str = ""

    @property
    def label(self) -> str:
        return phase_label(self.phase)

    @property
    def round_label(self) -> str:
        if self.round_total > 0:
            return f"round {self.round_index}/{self.round_total}"
        return ""


def format_status(event: ProgressEvent) -> str:
    """Render one progress event as a single line of plain text."""
    parts: List[str] = [event.label]
    if event.round_label:
        parts.append(event.round_label)
    parts.append(f"{format_tokens(event.tokens_used)} tokens")
    parts.append(f"{max(event.elapsed_s, 0.0):.1f}s")
    if event.detail:
        parts.append(" ".join(str(event.detail).split()))
    return " · ".join(part for part in parts if part)


class ProgressReporter:
    """Callback interface for phase/round progress.

    The base implementation only records events (useful for tests and for
    embedding the run in a larger UI); it never writes anything.
    """

    enabled = False

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._started_at: Optional[float] = None
        self.events: List[ProgressEvent] = []
        self.state = ProgressEvent(phase="idle")

    # -- reporting API ---------------------------------------------------
    def report(
        self,
        phase: str,
        *,
        round_index: int = 0,
        round_total: int = 0,
        tokens_used: int = 0,
        detail: str = "",
    ) -> ProgressEvent:
        """Record a phase transition and render it (if this reporter renders)."""
        if self._started_at is None:
            self._started_at = self._clock()
        event = ProgressEvent(
            phase=phase,
            round_index=max(int(round_index), 0),
            round_total=max(int(round_total), 0),
            tokens_used=max(int(tokens_used), 0),
            elapsed_s=max(self._clock() - self._started_at, 0.0),
            detail=detail,
        )
        self.state = event
        self.events.append(event)
        self._render(event)
        return event

    def finish(self, detail: str = "") -> None:
        """Mark the run as done and tear down any live rendering."""
        if self.events:
            self.report("done", detail=detail)
        self.close()

    def close(self) -> None:
        """Stop any rendering resources.  Safe to call more than once."""

    # -- hooks -----------------------------------------------------------
    def _render(self, event: ProgressEvent) -> None:
        """Subclasses render here; the base reporter is silent."""

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "ProgressReporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.finish()


class NullProgressReporter(ProgressReporter):
    """Silent reporter — used for ``--json`` runs and non-TTY output."""

    enabled = False


class LiveProgressReporter(ProgressReporter):
    """Single-line ``rich.Live`` status row for interactive terminals."""

    enabled = True

    def __init__(
        self,
        console: Console,
        *,
        refresh_per_second: float = 8,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(clock=clock)
        self.console = console
        self.refresh_per_second = refresh_per_second
        self._live: Optional[Live] = None
        self._closed = False

    def _render(self, event: ProgressEvent) -> None:
        text = Text(format_status(event), style="dim")
        if self._live is None:
            self._live = Live(
                text,
                console=self.console,
                refresh_per_second=self.refresh_per_second,
                transient=True,
            )
            self._live.start()
        else:
            self._live.update(text, refresh=True)

    def close(self) -> None:
        live, self._live = self._live, None
        self._closed = True
        if live is not None:
            try:
                live.stop()
            except Exception:  # pragma: no cover - defensive teardown
                pass

    def __del__(self):  # pragma: no cover - defensive teardown
        try:
            self.close()
        except Exception:
            pass


def _is_tty(console: Optional[Console]) -> bool:
    return bool(getattr(console, "is_terminal", False))


def build_progress_reporter(
    console: Optional[Console] = None,
    *,
    json_mode: bool = False,
) -> ProgressReporter:
    """Pick the right reporter for the current output mode.

    ``--json`` runs, captured/CI output (no TTY) and ``SMARTBENCH_NO_PROGRESS``
    all get the silent reporter so machine output stays clean.
    """
    if json_mode:
        return NullProgressReporter()
    if os.environ.get(DISABLE_ENV_VAR):
        return NullProgressReporter()
    console = console if console is not None else Console()
    if not _is_tty(console):
        return NullProgressReporter()
    return LiveProgressReporter(console)
