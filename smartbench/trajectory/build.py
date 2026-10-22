"""Build a versioned trajectory document from one audit run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from smartbench.trajectory.redact import redact_document
from smartbench.trajectory.schema import SCHEMA_VERSION, finding_signature


def build_trajectory(
    *,
    project_path: str | Path,
    tool_version: str,
    config: Dict[str, Any],
    session: Any = None,
    debate_result: Any = None,
    usage: Optional[Dict[str, Any]] = None,
    verification: Optional[Iterable[Dict[str, Any]]] = None,
    human_feedback: Optional[Iterable[Dict[str, Any]]] = None,
    adjudication: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return a redacted ``smartbench.trajectory/v1`` document.

    The trajectory keeps evidence references and verdicts, not raw prompts or
    model responses. ``usage`` is the optional trial/token block produced by
    the quick path; ``verification`` and ``human_feedback`` are appended when
    the caller has them (sandbox results, adjudication labels).
    """
    root = Path(project_path).expanduser().resolve()
    project_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    deterministic = _deterministic_findings(session)
    findings = _final_findings(debate_result) or list(deterministic)
    fingerprint = getattr(session, "fingerprint", None)
    languages = _languages(fingerprint, config)
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    trajectory_id = hashlib.sha256(
        f"{project_hash}:{recorded_at}:{finding_signature(findings)}".encode("utf-8")
    ).hexdigest()[:16]

    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trajectory_id": trajectory_id,
        "recorded_at": recorded_at,
        "tool_version": tool_version,
        "project": {
            "name": root.name,
            "root_hash": project_hash,
            "languages": languages,
        },
        "config": dict(config),
        "stages": _stages(session, debate_result),
        "deterministic": {
            "findings": deterministic,
            "finding_signature": finding_signature(deterministic),
            "errors": _bounded_errors(session),
        },
        "debate": _debate_summary(debate_result),
        "findings": findings,
        "finding_signature": finding_signature(findings),
        "adjudication": list(adjudication or []),
        "verification": list(verification or []),
        "human_feedback": list(human_feedback or []),
        "usage": dict(usage or {}),
        "analysis": _analysis_summary(debate_result),
    }
    if debate_result is not None:
        document["review_status"] = getattr(debate_result, "review_status", "")
    document = redact_document(document, project_root=root)
    # Redaction can change titles/paths, so signatures are computed last.
    document["finding_signature"] = finding_signature(document["findings"])
    deterministic_block = document.get("deterministic") or {}
    deterministic_block["finding_signature"] = finding_signature(
        deterministic_block.get("findings") or []
    )
    document["deterministic"] = deterministic_block
    return document


def _analysis_summary(debate_result: Any) -> Dict[str, Any]:
    """Keep the execution-state fields that explain how findings were merged."""
    report = getattr(debate_result, "analysis_report", None) if debate_result else None
    if not isinstance(report, dict):
        return {}
    summary: Dict[str, Any] = {}
    for key in ("review_strategy", "shard_plan", "merge", "shards", "stage_statuses"):
        if key in report:
            summary[key] = report[key]
    return summary


def _deterministic_findings(session: Any) -> List[Dict[str, Any]]:
    result = getattr(session, "result", None) if session is not None else None
    findings = getattr(result, "findings", None) or []
    normalized: List[Dict[str, Any]] = []
    for finding in findings:
        location = getattr(finding, "location", None)
        normalized.append(
            {
                "rule_id": str(getattr(finding, "rule_id", "") or ""),
                "file_path": _relative(getattr(location, "file_path", "")),
                "line_start": _as_int(getattr(location, "line_start", 0)),
                "title": str(getattr(finding, "rule_name", "") or ""),
                "severity": str(getattr(finding, "severity", "") or ""),
                "confidence": _as_float(getattr(finding, "confidence", 0.0)),
                "evidence_ids": [],
            }
        )
    return normalized


def _final_findings(debate_result: Any) -> List[Dict[str, Any]]:
    suggestions = getattr(debate_result, "final_suggestions", None) or []
    normalized: List[Dict[str, Any]] = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        file_path, line_start = _location(suggestion)
        normalized.append(
            {
                "rule_id": str(suggestion.get("rule_id") or ""),
                "file_path": _relative(file_path),
                "line_start": line_start,
                "title": str(suggestion.get("title") or ""),
                "severity": str(suggestion.get("severity") or ""),
                "confidence": _as_float(suggestion.get("confidence")),
                "evidence_ids": [
                    str(item)
                    for item in suggestion.get("evidence", []) or []
                ][:20],
                "status": str(suggestion.get("status") or "suggested"),
            }
        )
    return normalized


def _stages(session: Any, debate_result: Any) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = [
        {
            "name": "deterministic",
            "status": "errors" if _bounded_errors(session) else "ok",
            "findings": len(_deterministic_findings(session)),
        }
    ]
    if debate_result is not None:
        stages.append(
            {
                "name": "debate",
                "status": str(getattr(debate_result, "review_status", "") or "unknown"),
                "iterations": _as_int(getattr(debate_result, "iterations", 0)),
                "duration_ms": _as_int(getattr(debate_result, "duration_ms", 0)),
                "consensus": bool(getattr(debate_result, "consensus_reached", False)),
                "stage_statuses": {
                    str(key): str(value)
                    for key, value in (
                        getattr(debate_result, "stage_statuses", None) or {}
                    ).items()
                },
            }
        )
    return stages


def _debate_summary(debate_result: Any) -> List[Dict[str, Any]]:
    log = getattr(debate_result, "debate_log", None) or []
    summary: List[Dict[str, Any]] = []
    for entry in log:
        if not isinstance(entry, dict):
            continue
        payload = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        summary.append(
            {
                "role": str(entry.get("role") or ""),
                "round": _as_int(entry.get("round", 0)),
                "output_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12],
                "output_chars": len(payload),
            }
        )
    return summary[:40]


def _bounded_errors(session: Any) -> List[str]:
    result = getattr(session, "result", None) if session is not None else None
    errors = getattr(result, "errors", None) or []
    return [str(error)[:300] for error in errors[:20]]


def _languages(fingerprint: Any, config: Dict[str, Any]) -> List[str]:
    configured = config.get("languages")
    if isinstance(configured, list) and configured:
        return [str(item) for item in configured]
    languages = getattr(fingerprint, "languages", None)
    if isinstance(languages, (list, tuple)) and languages:
        return [str(item) for item in languages]
    primary = getattr(fingerprint, "primary_language", None)
    if primary:
        return [str(getattr(primary, "value", primary))]
    return []


def _location(suggestion: Dict[str, Any]) -> tuple[str, int]:
    location = suggestion.get("location")
    file_path = str(suggestion.get("file_path") or "")
    line_start = _as_int(suggestion.get("line_start", 0))
    if isinstance(location, dict):
        file_path = str(location.get("file_path") or file_path)
        line_start = _as_int(location.get("line_start", line_start))
    elif isinstance(location, str):
        path_part, _, line_part = location.partition(":")
        file_path = path_part or file_path
        line_start = _as_int(line_part.split("-")[0], line_start)
    return file_path, line_start


def _relative(path: Any) -> str:
    """Normalize a path without leaking an absolute location."""
    text = str(path or "").replace("\\", "/")
    if text.startswith("~"):
        return text
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.name
    while text.startswith("./"):
        text = text[2:]
    return text


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return default
