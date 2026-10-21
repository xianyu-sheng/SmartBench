"""Versioned human-feedback events (``smartbench.feedback/v1``).

One event records a human decision about one finding: confirm it is actionable,
or reject it as a trade-off / intentional / out-of-scope / theoretical. Events
are user-side data; they are used for offline ranking and advisory thresholds,
never to suppress a finding.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from smartbench.analysis.adjudication import Adjudication

SCHEMA_VERSION = "smartbench.feedback/v1"

LABELS = tuple(
    item.value for item in Adjudication if item is not Adjudication.UNKNOWN
)


@dataclass(frozen=True)
class FeedbackEvent:
    event_id: str
    recorded_at: str
    label: str
    rule_id: str = ""
    language: str = ""
    file_path: str = ""
    line_start: int = 0
    confidence: float | None = None
    reason: str = ""
    source: str = ""
    trajectory_id: str = ""
    finding_signature: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "recorded_at": self.recorded_at,
            "label": self.label,
            "rule_id": self.rule_id,
            "language": self.language,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "reason": self.reason,
            "source": self.source,
            "trajectory_id": self.trajectory_id,
            "finding_signature": self.finding_signature,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FeedbackEvent | None":
        if raw.get("schema_version") != SCHEMA_VERSION:
            return None
        label = str(raw.get("label", "")).strip()
        if label not in LABELS:
            return None
        try:
            line_start = int(raw.get("line_start", 0) or 0)
        except (TypeError, ValueError):
            line_start = 0
        confidence = raw.get("confidence")
        try:
            confidence_value = None if confidence is None else float(confidence)
        except (TypeError, ValueError):
            confidence_value = None
        return cls(
            event_id=str(raw.get("event_id", "")).strip(),
            recorded_at=str(raw.get("recorded_at", "")).strip(),
            label=label,
            rule_id=str(raw.get("rule_id", "")).strip(),
            language=str(raw.get("language", "")).strip().lower(),
            file_path=str(raw.get("file_path", "")).strip(),
            line_start=line_start,
            confidence=confidence_value,
            reason=str(raw.get("reason", "")).strip(),
            source=str(raw.get("source", "")).strip(),
            trajectory_id=str(raw.get("trajectory_id", "")).strip(),
            finding_signature=str(raw.get("finding_signature", "")).strip(),
        )


def make_event(
    *,
    label: str,
    rule_id: str = "",
    language: str = "",
    file_path: str = "",
    line_start: int = 0,
    confidence: float | None = None,
    reason: str = "",
    source: str = "",
    trajectory_id: str = "",
    finding_signature: str = "",
    recorded_at: str | None = None,
) -> FeedbackEvent:
    """Build an event with a deterministic id (duplicates collapse)."""
    if label not in LABELS:
        allowed = ", ".join(LABELS)
        raise ValueError(f"label must be one of: {allowed}")
    normalized_language = language.strip().lower()
    identity = json.dumps(
        {
            "label": label,
            "rule_id": rule_id.strip(),
            "language": normalized_language,
            "file_path": file_path.strip(),
            "line_start": int(line_start),
            "reason": reason.strip(),
            "source": source.strip(),
            "trajectory_id": trajectory_id.strip(),
            "finding_signature": finding_signature.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return FeedbackEvent(
        event_id=event_id,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        label=label,
        rule_id=rule_id.strip(),
        language=normalized_language,
        file_path=file_path.strip(),
        line_start=int(line_start),
        confidence=None if confidence is None else round(float(confidence), 4),
        reason=reason.strip(),
        source=source.strip(),
        trajectory_id=trajectory_id.strip(),
        finding_signature=finding_signature.strip(),
    )


def validate_event(event: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(event, Mapping):
        return ["event must be an object"]
    if event.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if not str(event.get("event_id", "")).strip():
        errors.append("event_id is required")
    if not str(event.get("recorded_at", "")).strip():
        errors.append("recorded_at is required")
    if str(event.get("label", "")).strip() not in LABELS:
        errors.append("label must be one of: " + ", ".join(LABELS))
    try:
        int(event.get("line_start", 0) or 0)
    except (TypeError, ValueError):
        errors.append("line_start must be an integer")
    confidence = event.get("confidence")
    if confidence is not None:
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            errors.append("confidence must be a number")
        else:
            if not 0.0 <= value <= 1.0:
                errors.append("confidence must be between 0 and 1")
    return errors
