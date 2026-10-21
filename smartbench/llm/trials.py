"""Trial-level stability accounting for the LLM review path (issue #12).

A trial is one complete LLM review over the same deterministic analysis. The
signature of a trial is the normalized set of final suggestions; two trials
agree when their signatures match. Stability is the share of trials that agree
with the most common signature, and ``stable`` is true only when every trial
agrees. Differing trials stay visible in the report instead of being averaged
away.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

TRIALS_SCHEMA_VERSION = "smartbench.trials/v1"


@dataclass
class UsageAccumulator:
    """Collect normalized usage records from one trial."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    model: str | None = None

    def add(self, usage: Dict[str, Any]) -> None:
        try:
            self.prompt_tokens += max(0, int(usage.get("prompt_tokens", 0) or 0))
            self.completion_tokens += max(
                0, int(usage.get("completion_tokens", 0) or 0)
            )
        except (TypeError, ValueError):
            pass
        model = usage.get("model")
        if isinstance(model, str) and model:
            self.model = model
        self.calls += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TrialRecord:
    index: int
    suggestions: int
    signature: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None
    duration_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "suggestions": self.suggestions,
            "signature": self.signature,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class TrialReport:
    trials: List[TrialRecord]
    temperature: float
    seed: int | None
    model: str | None
    stable: bool
    stability_rate: float
    majority_count: int
    total_tokens: int
    total_cost_usd: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": TRIALS_SCHEMA_VERSION,
            "trials": [trial.to_dict() for trial in self.trials],
            "count": len(self.trials),
            "temperature": self.temperature,
            "seed": self.seed,
            "model": self.model,
            "stable": self.stable,
            "stability_rate": self.stability_rate,
            "majority_count": self.majority_count,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
        }


def suggestion_signature(suggestions: Iterable[Dict[str, Any]] | None) -> str:
    """Hash the normalized final-suggestion set of one trial."""
    keys = sorted(_suggestion_key(item) for item in suggestions or [])
    canonical = json.dumps(keys, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def summarize_trials(
    trials: Sequence[TrialRecord],
    *,
    temperature: float,
    seed: int | None,
    model: str | None,
) -> TrialReport:
    """Aggregate trial records without hiding disagreement."""
    counts = Counter(trial.signature for trial in trials)
    majority_count = max(counts.values()) if counts else 0
    stability_rate = (majority_count / len(trials)) if trials else 0.0
    costs = [trial.cost_usd for trial in trials]
    total_cost = (
        round(sum(cost for cost in costs if cost is not None), 6)
        if costs and all(cost is not None for cost in costs)
        else None
    )
    return TrialReport(
        trials=list(trials),
        temperature=round(temperature, 4),
        seed=seed,
        model=model,
        stable=len(counts) <= 1,
        stability_rate=round(stability_rate, 4),
        majority_count=majority_count,
        total_tokens=sum(trial.total_tokens for trial in trials),
        total_cost_usd=total_cost,
    )


def _suggestion_key(suggestion: Dict[str, Any]) -> tuple[str, str, int, str]:
    if not isinstance(suggestion, dict):
        return ("", "", 0, str(suggestion))
    location = suggestion.get("location")
    file_path = str(suggestion.get("file_path") or "")
    line_start = _as_int(suggestion.get("line_start"))
    if isinstance(location, dict):
        file_path = str(location.get("file_path") or file_path)
        line_start = _as_int(location.get("line_start"), default=line_start)
    elif isinstance(location, str):
        path_part, _, line_part = location.partition(":")
        file_path = path_part or file_path
        line_start = _as_int(line_part.split("-")[0], default=line_start)
    return (
        str(suggestion.get("rule_id") or ""),
        file_path,
        line_start,
        str(suggestion.get("title") or ""),
    )


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
