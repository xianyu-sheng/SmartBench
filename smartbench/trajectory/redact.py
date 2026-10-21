"""Redaction for stored trajectories.

Trajectories must be safe to keep and export: no secrets, no absolute home
paths, bounded strings, project paths made relative where possible. Redaction
is best-effort on purpose — it is a safety net, not a guarantee.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_MASK = "[REDACTED]"
_MAX_STRING = 2000
_MAX_DEPTH = 8

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def redact_document(document: Any, *, project_root: str | Path | None = None) -> Any:
    """Recursively redact a trajectory-like structure."""
    root_text = _normalized_root(project_root)
    home_text = str(Path.home())
    return _redact_value(
        document,
        root_text=root_text,
        home_text=home_text,
        depth=0,
    )


def redact_text(text: str, *, project_root: str | Path | None = None) -> str:
    """Redact one string (exported for tests and store-level checks)."""
    value = _redact_value(
        text,
        root_text=_normalized_root(project_root),
        home_text=str(Path.home()),
        depth=0,
    )
    return value if isinstance(value, str) else ""


def _redact_value(
    value: Any,
    *,
    root_text: str | None,
    home_text: str,
    depth: int,
) -> Any:
    if depth > _MAX_DEPTH:
        return _MASK
    if isinstance(value, dict):
        return {
            _redact_string(str(key), root_text, home_text): _redact_value(
                item,
                root_text=root_text,
                home_text=home_text,
                depth=depth + 1,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _redact_value(
                item,
                root_text=root_text,
                home_text=home_text,
                depth=depth + 1,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _redact_string(value, root_text, home_text)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_string(str(value), root_text, home_text)


def _redact_string(value: str, root_text: str | None, home_text: str) -> str:
    text = value
    if root_text:
        text = text.replace(root_text, "<project>")
    if home_text and home_text != root_text:
        text = text.replace(home_text, "~")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_MASK, text)
    if len(text) > _MAX_STRING:
        text = text[:_MAX_STRING] + "…[truncated]"
    return text


def _normalized_root(project_root: str | Path | None) -> str | None:
    if project_root is None:
        return None
    try:
        return str(Path(project_root).expanduser().resolve())
    except OSError:
        return str(project_root)
