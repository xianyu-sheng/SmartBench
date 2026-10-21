"""Append-only, user-side storage for feedback events.

Default: ``~/.smartbench/feedback/events.jsonl`` (override with ``--file`` or
``SMARTBENCH_FEEDBACK_FILE``). Each line is one ``smartbench.feedback/v1``
event. Corrupt lines are skipped; duplicate event ids collapse on append.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List, Optional

from smartbench.feedback.schema import FeedbackEvent, validate_event

FEEDBACK_ENV_VAR = "SMARTBENCH_FEEDBACK_FILE"


def default_path() -> Path:
    override = os.environ.get(FEEDBACK_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".smartbench" / "feedback" / "events.jsonl"


class FeedbackStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else default_path()

    def events(self) -> List[FeedbackEvent]:
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: List[FeedbackEvent] = []
        for line in lines:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or validate_event(raw):
                continue
            event = FeedbackEvent.from_dict(raw)
            if event is not None:
                events.append(event)
        return events

    def append(self, event: FeedbackEvent) -> bool:
        """Append one event; return False when it is invalid or a duplicate."""
        if validate_event(event.to_dict()):
            return False
        if any(existing.event_id == event.event_id for existing in self.events()):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
                    + "\n"
                )
        except OSError:
            return False
        return True

    def filter(
        self,
        *,
        rule_id: str | None = None,
        language: str | None = None,
        label: str | None = None,
    ) -> List[FeedbackEvent]:
        selected = self.events()
        if rule_id:
            selected = [event for event in selected if event.rule_id == rule_id]
        if language:
            selected = [
                event
                for event in selected
                if event.language == language.strip().lower()
            ]
        if label:
            selected = [event for event in selected if event.label == label]
        return selected

    def counts(self, events: Optional[Iterable[FeedbackEvent]] = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in events if events is not None else self.events():
            counts[event.label] = counts.get(event.label, 0) + 1
        return counts
