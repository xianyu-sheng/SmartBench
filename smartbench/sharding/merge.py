"""Scope an evidence pack to one shard and merge per-shard debate results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from smartbench.engine.debate import DebateResult
from smartbench.ir import EvidencePack
from smartbench.sharding.plan import Shard, ShardPlan


@dataclass(frozen=True)
class ShardOutcome:
    shard_id: str
    status: str
    result: Optional[DebateResult] = None
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "shard_id": self.shard_id,
            "status": self.status,
            "error": self.error,
            "suggestions": len(getattr(self.result, "final_suggestions", []) or []),
        }


def scope_evidence_pack(pack: EvidencePack, shard: Shard) -> EvidencePack:
    """Keep facts/hypotheses whose evidence falls inside *shard*.

    Hypotheses carry no location contract, so they are scoped only when their
    attributes expose a file path; otherwise they are dropped for that shard.
    """
    files = {_normalize_path(path) for path in shard.files}
    facts = [
        fact
        for fact in pack.facts
        if any(_matches(ref.file_path, files) for ref in fact.evidence)
    ]
    hypotheses = [
        item
        for item in pack.hypotheses
        if _matches(str(item.attributes.get("file_path") or ""), files)
    ]
    return EvidencePack.from_facts(
        pack.query,
        facts,
        hypotheses=hypotheses,
        retrieval_trace=(*pack.retrieval_trace, f"shard:{shard.shard_id}"),
        graph_version=pack.graph_version,
    )


def merge_shard_results(
    outcomes: Sequence[ShardOutcome],
    *,
    plan: Optional[ShardPlan] = None,
) -> DebateResult:
    """Merge shard debates deterministically, deduplicating identical findings."""
    ordered = sorted(outcomes, key=lambda outcome: outcome.shard_id)
    best: Dict[Tuple[str, str, int], Tuple[Dict[str, Any], str]] = {}
    duplicates = 0
    conflicts = 0
    unreviewed: List[Dict[str, Any]] = []
    log: List[Dict[str, Any]] = []
    iterations = 0
    tokens = 0
    duration = 0
    completed = [outcome for outcome in ordered if outcome.status == "completed"]
    consensus = bool(completed) and all(
        bool(getattr(outcome.result, "consensus_reached", False))
        for outcome in completed
    )

    for outcome in ordered:
        result = outcome.result
        if result is None:
            continue
        for suggestion in result.final_suggestions:
            key = _suggestion_key(suggestion)
            existing = best.get(key)
            if existing is None:
                best[key] = (dict(suggestion), outcome.shard_id)
                continue
            duplicates += 1
            if _strength(suggestion) > _strength(existing[0]):
                best[key] = (dict(suggestion), outcome.shard_id)
                conflicts += 1
        unreviewed.extend(result.unreviewed_suggestions)
        for entry in result.debate_log:
            if isinstance(entry, dict):
                log.append({"shard": outcome.shard_id, **entry})
        iterations += int(getattr(result, "iterations", 0) or 0)
        tokens += int(getattr(result, "total_tokens_used", 0) or 0)
        duration = max(duration, int(getattr(result, "duration_ms", 0) or 0))

    suggestions = [
        suggestion
        for suggestion, _ in sorted(best.values(), key=lambda item: _sort_key(item[0]))
    ]
    statuses = {outcome.shard_id: outcome.status for outcome in ordered}
    review_status = (
        "completed"
        if ordered and all(outcome.status == "completed" for outcome in ordered)
        else "partial"
    )
    return DebateResult(
        final_suggestions=suggestions,
        unreviewed_suggestions=unreviewed,
        debate_log=log,
        consensus_reached=consensus and review_status == "completed",
        review_status=review_status,
        stage_statuses=statuses,
        iterations=iterations,
        total_tokens_used=tokens,
        duration_ms=duration,
        analysis_report={
            "review_strategy": "sharded",
            "shard_plan": plan.to_dict() if plan is not None else {},
            "shards": [outcome.to_dict() for outcome in ordered],
            "merge": {
                "suggestions": len(suggestions),
                "cross_shard_duplicates": duplicates,
                "conflicts_resolved": conflicts,
            },
        },
    )


def _suggestion_key(suggestion: Mapping[str, Any]) -> Tuple[str, str, int]:
    location = suggestion.get("location")
    file_path = str(suggestion.get("file_path") or "")
    line_start = _as_int(suggestion.get("line_start"))
    if isinstance(location, Mapping):
        file_path = str(location.get("file_path") or file_path)
        line_start = _as_int(location.get("line_start"), line_start)
    elif isinstance(location, str):
        path_part, _, line_part = location.partition(":")
        file_path = path_part or file_path
        line_start = _as_int(line_part.split("-")[0], line_start)
    return (
        str(suggestion.get("rule_id") or ""),
        file_path,
        line_start,
    )


def _sort_key(suggestion: Mapping[str, Any]) -> Tuple[str, int, str, str]:
    rule, file_path, line = _suggestion_key(suggestion)
    return (file_path, line, rule, str(suggestion.get("title") or ""))


def _strength(suggestion: Mapping[str, Any]) -> Tuple[float, int]:
    try:
        confidence = float(suggestion.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = suggestion.get("evidence")
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    return (confidence, evidence_count)


def _normalize_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _matches(value: str, files: set[str]) -> bool:
    path = _normalize_path(value)
    if not path:
        return False
    if path in files:
        return True
    return any(path.endswith("/" + name) or name.endswith("/" + path) for name in files)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
