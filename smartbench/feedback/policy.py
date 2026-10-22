"""Offline ranking and advisory thresholds from human feedback.

Two rules that come straight from the project's evidence discipline:

1. **Ranking only.** A prior may reorder findings and annotate them; it can
   never remove one. ``suppressed`` in the output is always 0.
2. **Thresholds are advisory.** A suggested ``min_confidence`` is printed for a
   human to adopt via configuration; it is never applied silently.

Priors use Laplace smoothing so a single rejection does not dominate:
``(actionable + 1) / (support + 2)``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from smartbench.analysis.adjudication import Adjudication
from smartbench.feedback.schema import FeedbackEvent

RANK_SCHEMA_VERSION = "smartbench.feedback.rank/v1"
ACTIONABLE = Adjudication.ACTIONABLE.value
MIN_ADVISORY_SUPPORT = 3

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".swift": "swift",
}


@dataclass(frozen=True)
class Prior:
    rule_id: str
    language: str
    support: int
    actionable: int
    non_actionable: int
    actionable_ratio: float
    label_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "language": self.language,
            "support": self.support,
            "actionable": self.actionable,
            "non_actionable": self.non_actionable,
            "actionable_ratio": self.actionable_ratio,
            "label_counts": dict(self.label_counts),
        }


def build_priors(
    events: Iterable[FeedbackEvent],
) -> Dict[Tuple[str, str], Prior]:
    """Aggregate events into (rule, language) priors, plus ``*`` fallbacks."""
    buckets: Dict[Tuple[str, str], Counter[str]] = defaultdict(Counter)
    for event in events:
        key = (event.rule_id or "*", event.language or "*")
        buckets[key][event.label] += 1
    return {key: _prior(key, counts) for key, counts in buckets.items()}


def prior_for(
    priors: Mapping[Tuple[str, str], Prior],
    rule_id: str,
    language: str,
) -> Prior | None:
    """Prefer an exact match, then a rule-only prior, then a language-only one."""
    rule = rule_id or "*"
    lang = language or "*"
    for key in ((rule, lang), (rule, "*"), ("*", lang)):
        if key in priors:
            return priors[key]
    return None


def rank_findings(
    findings: Sequence[Mapping[str, Any]],
    events: Iterable[FeedbackEvent],
    *,
    default_score: float = 0.5,
) -> dict[str, object]:
    """Return a ranked copy of *findings*; every input finding is preserved."""
    event_list = list(events)
    priors = build_priors(event_list)
    ordered: List[Tuple[float, float, int, Dict[str, Any]]] = []
    for index, finding in enumerate(findings):
        item = dict(finding)
        rule_id = str(item.get("rule_id") or "")
        language = finding_language(item)
        prior = prior_for(priors, rule_id, language)
        confidence = _confidence(item)
        score = prior.actionable_ratio if prior is not None else default_score
        item["feedback"] = (
            {
                "score": score,
                "actionable_ratio": prior.actionable_ratio,
                "support": prior.support,
                "label_counts": dict(prior.label_counts),
            }
            if prior is not None
            else {
                "score": score,
                "actionable_ratio": None,
                "support": 0,
                "label_counts": {},
            }
        )
        ordered.append((score, confidence, index, item))
    ordered.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
    return {
        "schema_version": RANK_SCHEMA_VERSION,
        "findings": [entry[3] for entry in ordered],
        "policy": {
            "ranked": len(ordered),
            "suppressed": 0,
            "note": "ranking only; no finding is dropped",
            "priors": [prior.to_dict() for _, prior in _sorted_priors(priors)],
            "advisory_thresholds": suggest_thresholds(event_list, priors=priors),
        },
    }


def suggest_thresholds(
    events: Iterable[FeedbackEvent],
    *,
    priors: Mapping[Tuple[str, str], Prior] | None = None,
    min_support: int = MIN_ADVISORY_SUPPORT,
) -> List[dict[str, object]]:
    """Suggest advisory thresholds for priors dominated by non-actionable labels."""
    event_list = list(events)
    active = dict(priors) if priors is not None else build_priors(event_list)
    suggestions: List[dict[str, object]] = []
    for (rule, language), prior in _sorted_priors(active):
        if prior.support < min_support or prior.actionable_ratio >= 0.5:
            continue
        rejected = [
            event.confidence
            for event in event_list
            if (event.rule_id or "*", event.language or "*") == (rule, language)
            and event.label != ACTIONABLE
            and event.confidence is not None
        ]
        suggestions.append(
            {
                "rule_id": rule,
                "language": language,
                "support": prior.support,
                "actionable_ratio": prior.actionable_ratio,
                "suggested_min_confidence": max(rejected) if rejected else None,
                "note": "advisory only; findings are never suppressed",
            }
        )
    return suggestions


def _prior(key: Tuple[str, str], counts: Counter[str]) -> Prior:
    total = sum(counts.values())
    actionable = counts.get(ACTIONABLE, 0)
    ratio = round((actionable + 1) / (total + 2), 4)
    return Prior(
        rule_id=key[0],
        language=key[1],
        support=total,
        actionable=actionable,
        non_actionable=total - actionable,
        actionable_ratio=ratio,
        label_counts=dict(counts),
    )


def _sorted_priors(
    priors: Mapping[Tuple[str, str], Prior]
) -> List[Tuple[Tuple[str, str], Prior]]:
    return sorted(priors.items(), key=lambda item: item[0])


def finding_language(finding: Mapping[str, Any]) -> str:
    explicit = str(finding.get("language") or "").strip().lower()
    if explicit:
        return explicit
    location = finding.get("location")
    path = ""
    if isinstance(location, Mapping):
        path = str(location.get("file_path") or "")
    if not path:
        path = str(finding.get("file_path") or "")
    for suffix, language in _LANGUAGE_BY_SUFFIX.items():
        if path.endswith(suffix):
            return language
    return ""


def _confidence(finding: Mapping[str, Any]) -> float:
    try:
        return float(finding.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0
