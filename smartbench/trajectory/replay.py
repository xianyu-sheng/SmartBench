"""Offline replay checks for stored trajectories.

Full replay of an LLM conclusion is not claimed: the model is skipped. What we
can reproduce is the deterministic floor. ``replay_document`` validates the
schema and, when a project path is supplied, re-runs the deterministic analysis
and compares its findings with the recorded ``deterministic`` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Set, Tuple

from smartbench.trajectory.schema import validate_document


def replay_document(
    document: Dict[str, Any],
    *,
    project_path: str | Path | None = None,
) -> Dict[str, Any]:
    errors = validate_document(document)
    result: Dict[str, Any] = {
        "ok": not errors,
        "errors": errors,
        "trajectory_id": document.get("trajectory_id") if isinstance(document, dict) else None,
        "llm": "skipped (deterministic replay only)",
    }
    if errors:
        return result

    stored = _stored_findings(document)
    result["stored_deterministic_findings"] = len(stored)
    result["stored_finding_signature"] = document.get("finding_signature")

    if project_path is None:
        result["reproduced"] = "structural"
        return result

    replayed = _replayed_findings(Path(project_path))
    stored_keys = _keys(stored)
    replayed_keys = _keys(replayed)
    matched = stored_keys & replayed_keys
    result.update(
        {
            "reproduced": "deterministic",
            "replayed_deterministic_findings": len(replayed),
            "matched": len(matched),
            "only_recorded": sorted(stored_keys - replayed_keys),
            "only_replayed": sorted(replayed_keys - stored_keys),
        }
    )
    result["ok"] = not result["only_recorded"] and not result["only_replayed"]
    return result


def _stored_findings(document: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    deterministic = document.get("deterministic")
    if isinstance(deterministic, dict):
        findings = deterministic.get("findings")
        if isinstance(findings, list):
            return findings
    return []


def _replayed_findings(project_path: Path) -> list[Dict[str, Any]]:
    from smartbench.core import AnalysisSession

    session = AnalysisSession.analyze(project_path)
    result = getattr(session, "result", None)
    findings = getattr(result, "findings", None) or []
    normalized: list[Dict[str, Any]] = []
    for finding in findings:
        location = getattr(finding, "location", None)
        normalized.append(
            {
                "rule_id": str(getattr(finding, "rule_id", "") or ""),
                "file_path": _normalize_path(getattr(location, "file_path", "")),
                "line_start": _as_int(getattr(location, "line_start", 0)),
            }
        )
    return normalized


def _keys(findings: Iterable[Dict[str, Any]]) -> Set[Tuple[str, str, int]]:
    return {
        (
            str(item.get("rule_id") or ""),
            _normalize_path(item.get("file_path", "")),
            _as_int(item.get("line_start", 0)),
        )
        for item in findings
        if isinstance(item, dict)
    }


def _normalize_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
