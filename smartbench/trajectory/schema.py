"""Versioned audit-trajectory schema (``smartbench.trajectory/v1``).

A trajectory records one audit run: deterministic facts, debate stages,
findings, adjudication, verification and usage. It deliberately does **not**
store raw prompts, raw model responses or secrets, and file paths are kept
project-relative. The schema is validated before anything is written.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "smartbench.trajectory/v1"

_REQUIRED_TOP_LEVEL = (
    "schema_version",
    "trajectory_id",
    "recorded_at",
    "tool_version",
    "project",
    "config",
    "findings",
)


def finding_signature(findings: Iterable[Mapping[str, Any]]) -> str:
    """Return a stable signature over the normalized finding set."""
    keys = sorted(
        (
            str(item.get("rule_id") or ""),
            str(item.get("file_path") or ""),
            _as_int(item.get("line_start")),
            str(item.get("title") or ""),
        )
        for item in findings
        if isinstance(item, Mapping)
    )
    canonical = json.dumps(keys, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def validate_document(document: Any) -> list[str]:
    """Return a list of schema errors; empty means valid."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["document must be an object"]
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    for key in _REQUIRED_TOP_LEVEL:
        if key not in document:
            errors.append(f"missing required field: {key}")
    for key in ("trajectory_id", "recorded_at", "tool_version"):
        if key in document and not isinstance(document.get(key), str):
            errors.append(f"{key} must be a string")
    project = document.get("project")
    if not isinstance(project, dict) or not project.get("name"):
        errors.append("project.name is required")
    if isinstance(project, dict) and "repository" in project:
        if not isinstance(project.get("repository"), str):
            errors.append("project.repository must be a string")
    if not isinstance(document.get("config"), dict):
        errors.append("config must be an object")
    findings = document.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
    else:
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                errors.append(f"findings[{index}] must be an object")
                continue
            for key in ("rule_id", "file_path", "line_start"):
                if key not in finding:
                    errors.append(f"findings[{index}].{key} is required")
    signature = document.get("finding_signature")
    if signature is not None and isinstance(findings, list):
        expected = finding_signature(findings)
        if signature != expected:
            errors.append("finding_signature does not match the findings")
    return errors


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
