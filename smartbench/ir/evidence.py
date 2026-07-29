"""Evidence and fact contracts shared by deterministic analyzers and agents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class FactKind(str, Enum):
    """Stable predicates used in the semantic knowledge graph."""

    DEFINES = "defines"
    REFERENCES = "references"
    CALLS = "calls"
    IMPORTS = "imports"
    READS = "reads"
    WRITES = "writes"
    FLOWS_TO = "flows_to"
    CONTROLS = "controls"
    SANITIZES = "sanitizes"
    SOURCE = "source"
    SINK = "sink"
    STATE_TRANSITION = "state_transition"
    TEST_COVERS = "test_covers"


@dataclass(frozen=True)
class EvidenceRef:
    """A source-backed reference that an agent can independently verify."""

    file_path: str
    line_start: int
    line_end: int | None = None
    column_start: int | None = None
    column_end: int | None = None
    snippet: str = ""
    source: str = "deterministic"

    def __post_init__(self) -> None:
        if self.line_end is None:
            object.__setattr__(self, "line_end", self.line_start)

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "column_start": self.column_start,
            "column_end": self.column_end,
            "snippet": self.snippet,
            "source": self.source,
        }


@dataclass(frozen=True)
class SemanticFact:
    """A graph fact with provenance and no implicit model-generated claims."""

    subject: str
    predicate: FactKind | str
    object: str
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("fact confidence must be between 0 and 1")

    @property
    def kind(self) -> FactKind | str:
        return self.predicate

    @property
    def fact_id(self) -> str:
        """Stable content address used by evidence-constrained agents."""
        encoded = json.dumps(
            self._identity_dict(),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return "fact-" + hashlib.sha256(encoded).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {"fact_id": self.fact_id, **self._identity_dict()}

    def _identity_dict(self) -> dict[str, object]:
        predicate = self.predicate.value if isinstance(self.predicate, FactKind) else self.predicate
        return {
            "subject": self.subject,
            "predicate": predicate,
            "object": self.object,
            "evidence": [ref.to_dict() for ref in self.evidence],
            "confidence": self.confidence,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class SemanticHypothesis:
    """An explicitly untrusted interpretation kept outside the fact set."""

    kind: str
    statement: str
    source: str = "agent"
    confidence: float = 0.0
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("hypothesis kind must not be empty")
        if not self.statement.strip():
            raise ValueError("hypothesis statement must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("hypothesis confidence must be between 0 and 1")

    @property
    def hypothesis_id(self) -> str:
        encoded = json.dumps(
            self._identity_dict(),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return "hypothesis-" + hashlib.sha256(encoded).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {"hypothesis_id": self.hypothesis_id, **self._identity_dict()}

    def _identity_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "statement": self.statement,
            "source": self.source,
            "confidence": self.confidence,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class EvidencePack:
    """Bounded context handed to the proposer/critic/judge agents.

    Agents may reason over this pack, but every factual claim in a final
    finding must cite one or more ``fact_ids`` or ``evidence`` references.
    """

    query: str
    facts: tuple[SemanticFact, ...] = field(default_factory=tuple)
    hypotheses: tuple[SemanticHypothesis, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    retrieval_trace: tuple[str, ...] = field(default_factory=tuple)
    graph_version: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "facts": [fact.to_dict() for fact in self.facts],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "evidence": [ref.to_dict() for ref in self.evidence],
            "retrieval_trace": list(self.retrieval_trace),
            "graph_version": self.graph_version,
        }

    @classmethod
    def from_facts(
        cls,
        query: str,
        facts: Sequence[SemanticFact],
        hypotheses: Sequence[SemanticHypothesis] = (),
        retrieval_trace: Sequence[str] = (),
        graph_version: str = "",
    ) -> "EvidencePack":
        refs: list[EvidenceRef] = []
        seen: set[tuple[str, int, int]] = set()
        for fact in facts:
            for ref in fact.evidence:
                key = (ref.file_path, ref.line_start, ref.line_end or ref.line_start)
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)
        return cls(
            query=query,
            facts=tuple(facts),
            hypotheses=tuple(hypotheses),
            evidence=tuple(refs),
            retrieval_trace=tuple(retrieval_trace),
            graph_version=graph_version,
        )
