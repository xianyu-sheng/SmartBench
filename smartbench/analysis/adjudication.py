"""Adjudicate findings into an actionable decision label.

The evidence gate decides whether a claim is grounded; this layer decides how a
grounded finding should be *handled*:

- ``actionable``: a real, worth-fixing finding;
- ``design_trade_off``: the fix changes behaviour under the failure that
  triggers it (for example an immediate retry that replaces fail-stop with a
  busy loop), or documented intent marks it as an accepted trade-off;
- ``intentional``: comments or documentation describe the behaviour as
  deliberate (fail-stop, by design);
- ``out_of_scope``: documentation marks the behaviour as explicitly unsupported;
- ``theoretical``: the trigger is not reachable in normal operation;
- ``unknown``: no basis to decide.

Adjudication never suppresses a finding and never fabricates intent: every
non-default label must cite either an intent fact, a rule-declared patch risk,
a reachability statement, or a user-declared memory entry.  The label is
advisory metadata for the human review step before a PR is filed.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from smartbench.analysis.intent import IntentFact, IntentKind, IntentPack
from smartbench.ir import EvidenceRef

MEMORY_SCHEMA_VERSION = "smartbench.intent/v1"
MEMORY_ENV_VAR = "SMARTBENCH_INTENT_FILE"


class Adjudication(str, Enum):
    """Bounded handling decision attached to a finding."""

    ACTIONABLE = "actionable"
    DESIGN_TRADE_OFF = "design_trade_off"
    INTENTIONAL = "intentional"
    OUT_OF_SCOPE = "out_of_scope"
    THEORETICAL = "theoretical"
    UNKNOWN = "unknown"


_INTENT_LABELS: Mapping[IntentKind, Adjudication] = {
    IntentKind.FAIL_STOP: Adjudication.INTENTIONAL,
    IntentKind.INTENTIONAL: Adjudication.INTENTIONAL,
    IntentKind.TRADE_OFF: Adjudication.DESIGN_TRADE_OFF,
    IntentKind.BOUNDED_RETRY: Adjudication.DESIGN_TRADE_OFF,
    IntentKind.OUT_OF_SCOPE: Adjudication.OUT_OF_SCOPE,
}

_LABEL_PRIORITY: Mapping[Adjudication, int] = {
    Adjudication.INTENTIONAL: 0,
    Adjudication.OUT_OF_SCOPE: 1,
    Adjudication.DESIGN_TRADE_OFF: 2,
}

_PATCH_RISK_LABEL = "regression_under_persistent_error"
_UNREACHABLE_VALUES = {"low", "unreachable", "rare", "unknown-trigger"}


@dataclass(frozen=True)
class MemoryEntry:
    """One user-declared handling rule for a repository pattern."""

    label: Adjudication
    reason: str = ""
    source: str = ""
    path: str = ""
    rule_id: str = ""

    def matches(self, finding: Any) -> bool:
        if self.rule_id and finding.rule_id != self.rule_id:
            return False
        if self.path:
            normalized = finding.location.file_path.replace("\\", "/").lstrip("./")
            if not fnmatch.fnmatch(normalized, self.path):
                return False
        return True


@dataclass(frozen=True)
class MemoryStore:
    """User-side intent memory (never written into the analyzed repository)."""

    entries: tuple[MemoryEntry, ...] = field(default_factory=tuple)

    def match(self, finding: Any) -> MemoryEntry | None:
        for entry in self.entries:
            if entry.matches(finding):
                return entry
        return None

    @classmethod
    def empty(cls) -> "MemoryStore":
        return cls()

    @classmethod
    def default_path(cls) -> Path:
        override = os.environ.get(MEMORY_ENV_VAR, "").strip()
        if override:
            return Path(override).expanduser()
        return Path.home() / ".smartbench" / "intent.yaml"

    @classmethod
    def load(cls, path: Path | None = None) -> "MemoryStore":
        target = Path(path).expanduser() if path is not None else cls.default_path()
        if not target.is_file():
            return cls.empty()
        try:
            document = yaml.safe_load(target.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return cls.empty()
        return cls.from_document(document)

    def with_entry(self, entry: MemoryEntry) -> "MemoryStore":
        """Return a store with one entry appended, skipping duplicates."""
        for existing in self.entries:
            if (
                existing.label is entry.label
                and existing.path == entry.path
                and existing.rule_id == entry.rule_id
                and existing.source == entry.source
            ):
                return self
        return MemoryStore(entries=(*self.entries, entry))

    def to_document(self) -> dict[str, object]:
        entries: list[dict[str, str]] = []
        for entry in self.entries:
            item: dict[str, str] = {"label": entry.label.value}
            if entry.path:
                item["path"] = entry.path
            if entry.rule_id:
                item["rule_id"] = entry.rule_id
            if entry.reason:
                item["reason"] = entry.reason
            if entry.source:
                item["source"] = entry.source
            entries.append(item)
        return {"version": MEMORY_SCHEMA_VERSION, "entries": entries}

    def save(self, path: Path | None = None) -> Path:
        target = Path(path).expanduser() if path is not None else self.default_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(self.to_document(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return target

    @classmethod
    def from_document(cls, document: object) -> "MemoryStore":
        if not isinstance(document, Mapping):
            return cls.empty()
        if document.get("version") != MEMORY_SCHEMA_VERSION:
            return cls.empty()
        raw_entries = document.get("entries", [])
        if not isinstance(raw_entries, list):
            return cls.empty()
        entries: list[MemoryEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                continue
            try:
                label = Adjudication(str(raw.get("label", "")).strip())
            except ValueError:
                continue
            entries.append(
                MemoryEntry(
                    label=label,
                    reason=str(raw.get("reason", "")).strip(),
                    source=str(raw.get("source", "")).strip(),
                    path=str(raw.get("path", "")).strip(),
                    rule_id=str(raw.get("rule_id", "")).strip(),
                )
            )
        return cls(entries=tuple(entries))


@dataclass(frozen=True)
class AdjudicationDecision:
    """The handling decision for one finding."""

    index: int
    rule_id: str
    file_path: str
    line_start: int
    label: Adjudication
    reasons: tuple[str, ...]
    basis: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "rule_id": self.rule_id,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "adjudication": self.label.value,
            "reasons": list(self.reasons),
            "basis": [reference.to_dict() for reference in self.basis],
        }


class Adjudicator:
    """Assign handling labels from intent, patch risk and reachability."""

    def adjudicate(
        self,
        findings: Sequence[Any],
        intent_pack: IntentPack | None = None,
        memory: MemoryStore | None = None,
    ) -> list[AdjudicationDecision]:
        pack = intent_pack or IntentPack()
        store = memory or MemoryStore.empty()
        decisions: list[AdjudicationDecision] = []
        for index, finding in enumerate(findings):
            decisions.append(self._adjudicate_one(index, finding, pack, store))
        return decisions

    def _adjudicate_one(
        self,
        index: int,
        finding: Any,
        intent_pack: IntentPack,
        memory: MemoryStore,
    ) -> AdjudicationDecision:
        basis: list[EvidenceRef] = []
        reasons: list[str] = []

        entry = memory.match(finding)
        if entry is not None:
            reasons.append(
                "declared in user intent memory"
                + (f": {entry.reason}" if entry.reason else "")
            )
            if entry.source:
                reasons.append(f"source: {entry.source}")
            return self._decision(index, finding, entry.label, reasons, ())

        facts = intent_pack.for_file(finding.location.file_path)
        if facts:
            label, fact, reason = self._label_from_intent(facts)
            if fact is not None:
                basis.append(fact.location)
            reasons.append(reason)
            return self._decision(index, finding, label, reasons, tuple(basis))

        patch_risk = str(finding.metadata.get("patch_risk", ""))
        if patch_risk == _PATCH_RISK_LABEL:
            reasons.append(
                "the proposed change retries under the same failure that "
                "triggers the defect"
            )
            return self._decision(
                index, finding, Adjudication.DESIGN_TRADE_OFF, reasons, ()
            )

        reachability = str(finding.metadata.get("reachability", "")).lower()
        if reachability in _UNREACHABLE_VALUES:
            reasons.append(
                f"trigger reachability is {reachability}; confirm it is "
                f"reachable in normal operation"
            )
            return self._decision(
                index, finding, Adjudication.THEORETICAL, reasons, ()
            )

        return self._decision(index, finding, Adjudication.ACTIONABLE, (), ())

    @staticmethod
    def _label_from_intent(
        facts: Sequence[IntentFact],
    ) -> tuple[Adjudication, IntentFact | None, str]:
        ranked = sorted(
            facts,
            key=lambda fact: (
                _LABEL_PRIORITY.get(_INTENT_LABELS[fact.kind], 99),
                fact.location.line_start,
            ),
        )
        fact = ranked[0]
        label = _INTENT_LABELS[fact.kind]
        reason = (
            f"repository text documents intent ``{fact.kind.value}``: "
            f"{fact.statement}"
        )
        return label, fact, reason

    @staticmethod
    def _decision(
        index: int,
        finding: Any,
        label: Adjudication,
        reasons: Sequence[str],
        basis: Sequence[EvidenceRef],
    ) -> AdjudicationDecision:
        return AdjudicationDecision(
            index=index,
            rule_id=finding.rule_id,
            file_path=finding.location.file_path,
            line_start=finding.location.line_start,
            label=label,
            reasons=tuple(reasons),
            basis=tuple(basis),
        )


def apply_adjudications(
    project_path: Path,
    findings: Sequence[Any],
    *,
    memory: MemoryStore | None = None,
    intent_pack: IntentPack | None = None,
) -> list[AdjudicationDecision]:
    """Attach adjudication metadata to findings in place."""
    from smartbench.analysis.intent import IntentScanner

    if intent_pack is None:
        intent_pack = IntentScanner().scan(project_path)
    if memory is None:
        memory = MemoryStore.load()
    decisions = Adjudicator().adjudicate(findings, intent_pack, memory)
    for finding, decision in zip(findings, decisions):
        finding.metadata["adjudication"] = decision.label.value
        if decision.reasons:
            finding.metadata["adjudication_reasons"] = list(decision.reasons)
        if decision.basis:
            finding.metadata["adjudication_basis"] = [
                reference.to_dict() for reference in decision.basis
            ]
    return decisions


def adjudication_counts(decisions: Iterable[AdjudicationDecision]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.label.value] = counts.get(decision.label.value, 0) + 1
    return counts


def validate_document(document: object) -> list[str]:
    """Return human-readable problems with an intent memory document."""
    errors: list[str] = []
    if not isinstance(document, Mapping):
        return ["document must be a mapping"]
    if document.get("version") != MEMORY_SCHEMA_VERSION:
        errors.append(
            f"version must be {MEMORY_SCHEMA_VERSION!r}, got {document.get('version')!r}"
        )
    entries = document.get("entries")
    if not isinstance(entries, list):
        errors.append("entries must be a list")
        return errors
    allowed = {"label", "path", "rule_id", "reason", "source"}
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{prefix} must be a mapping")
            continue
        unknown = sorted(set(entry) - allowed)
        if unknown:
            errors.append(f"{prefix} has unknown fields: {', '.join(unknown)}")
        try:
            Adjudication(str(entry.get("label", "")))
        except ValueError:
            errors.append(
                f"{prefix}.label must be one of "
                f"{', '.join(label.value for label in Adjudication)}"
            )
    return errors


@dataclass(frozen=True)
class _SuggestionView:
    """Adapter that lets agent suggestions use the finding adjudicator."""

    rule_id: str
    location: Any
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _SuggestionLocation:
    file_path: str
    line_start: int


def _suggestion_view(suggestion: Mapping[str, object]) -> _SuggestionView:
    location = suggestion.get("location")
    file_path = ""
    line_start = 0
    if isinstance(location, Mapping):
        file_path = str(location.get("file_path") or location.get("file") or "")
        try:
            line_start = int(location.get("line_start") or location.get("line") or 0)
        except (TypeError, ValueError):
            line_start = 0
    elif isinstance(location, str) and ":" in location:
        path, _, number = location.rpartition(":")
        file_path = path
        try:
            line_start = int(number)
        except ValueError:
            line_start = 0
    return _SuggestionView(
        rule_id=str(suggestion.get("rule_id", "") or ""),
        location=_SuggestionLocation(file_path=file_path, line_start=line_start),
        metadata={},
    )


def adjudicate_suggestions(
    project_path: Path,
    suggestions: Sequence[Any],
    *,
    memory: MemoryStore | None = None,
    intent_pack: IntentPack | None = None,
) -> list[AdjudicationDecision]:
    """Attach adjudication metadata to agent suggestions in place.

    Suggestions use ``file:line`` location strings and carry no rule-declared
    patch risk, so the decision comes from documented intent and user memory;
    everything else stays ``actionable``.
    """
    from smartbench.analysis.intent import IntentScanner

    views: list[_SuggestionView] = []
    for suggestion in suggestions:
        if isinstance(suggestion, Mapping):
            views.append(_suggestion_view(suggestion))
    if not views:
        return []
    if intent_pack is None:
        intent_pack = IntentScanner().scan(project_path)
    if memory is None:
        memory = MemoryStore.load()
    decisions = Adjudicator().adjudicate(views, intent_pack, memory)
    for suggestion, decision in zip(suggestions, decisions):
        if not isinstance(suggestion, dict):
            continue
        suggestion["adjudication"] = decision.label.value
        if decision.reasons:
            suggestion["adjudication_reasons"] = list(decision.reasons)
        if decision.basis:
            suggestion["adjudication_basis"] = [
                reference.to_dict() for reference in decision.basis
            ]
    return decisions
