"""Language-neutral, provenance-bearing type evidence contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from smartbench.ir.evidence import EvidenceRef

TYPE_EVIDENCE_SCHEMA_VERSION = "semantic-ir/type-evidence/v1"


class TypeEvidenceRole(str, Enum):
    """Semantic role of a type statement."""

    BINDING = "binding"
    RECEIVER = "receiver"
    RESULT = "result"


class TypeEvidenceSource(str, Enum):
    """How a provider obtained a type statement."""

    SURFACE_DECLARATION = "surface_declaration"
    SURFACE_FIELD = "surface_field"
    LOCAL_PROPAGATION = "local_propagation"
    TYPE_CHECKER = "type_checker"
    LIBRARY_CONTRACT = "library_contract"

    @property
    def rank(self) -> int:
        return {
            TypeEvidenceSource.LOCAL_PROPAGATION: 1,
            TypeEvidenceSource.SURFACE_DECLARATION: 2,
            TypeEvidenceSource.SURFACE_FIELD: 2,
            TypeEvidenceSource.TYPE_CHECKER: 3,
            TypeEvidenceSource.LIBRARY_CONTRACT: 3,
        }[self]


@dataclass(frozen=True)
class TypeEvidence:
    """One source-backed type assertion attached to a semantic operation."""

    operation_id: str
    role: TypeEvidenceRole
    type_name: str
    source: TypeEvidenceSource
    provider: str
    binding: str = ""
    position: int = -1
    canonical_symbol: str = ""
    confidence: float = 1.0
    evidence: tuple[EvidenceRef, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.operation_id.strip():
            raise ValueError("type evidence operation_id must not be empty")
        if not normalize_type_name(self.type_name):
            raise ValueError("type evidence type_name must not be empty")
        if not self.provider.strip():
            raise ValueError("type evidence provider must not be empty")
        if self.position < -1:
            raise ValueError("type evidence position must be >= -1")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("type evidence confidence must be between 0 and 1")

    @property
    def normalized_type(self) -> str:
        return normalize_type_name(self.type_name)

    @property
    def evidence_id(self) -> str:
        material = json.dumps(
            {
                "operation_id": self.operation_id,
                "role": self.role.value,
                "type_name": self.normalized_type,
                "source": self.source.value,
                "provider": self.provider,
                "binding": self.binding,
                "position": self.position,
                "canonical_symbol": self.canonical_symbol,
                "confidence": self.confidence,
                "evidence": [item.to_dict() for item in self.evidence],
                "attributes": dict(self.attributes),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return f"type-{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "operation_id": self.operation_id,
            "role": self.role.value,
            "type_name": self.type_name,
            "normalized_type": self.normalized_type,
            "source": self.source.value,
            "provider": self.provider,
            "binding": self.binding,
            "position": self.position,
            "canonical_symbol": self.canonical_symbol,
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "attributes": dict(self.attributes),
        }


class TypeEvidenceIndex:
    """Deterministic queries over evidence without language-specific logic."""

    def __init__(self, evidence: Iterable[TypeEvidence]) -> None:
        self._by_operation: dict[str, list[TypeEvidence]] = {}
        for item in sorted(evidence, key=_evidence_order):
            self._by_operation.setdefault(item.operation_id, []).append(item)

    def for_operation(
        self,
        operation_id: str,
        role: TypeEvidenceRole | None = None,
    ) -> tuple[TypeEvidence, ...]:
        values = self._by_operation.get(operation_id, ())
        return tuple(item for item in values if role is None or item.role == role)

    def unique_type(
        self,
        operation_id: str,
        role: TypeEvidenceRole,
    ) -> str:
        values = {
            item.normalized_type
            for item in self.for_operation(operation_id, role)
            if item.normalized_type
        }
        return next(iter(values)) if len(values) == 1 else ""

    def canonical_symbols(
        self,
        operation_id: str,
        role: TypeEvidenceRole = TypeEvidenceRole.RECEIVER,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.canonical_symbol
                    for item in self.for_operation(operation_id, role)
                    if item.canonical_symbol
                }
            )
        )

    def evidence_ids(
        self,
        operation_id: str,
        role: TypeEvidenceRole | None = None,
    ) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.for_operation(operation_id, role))


def normalize_type_name(value: str) -> str:
    """Normalize pointer/reference spelling without inventing type equivalence."""
    normalized = "".join(str(value).strip().split()).strip("'\"")
    while normalized.startswith(("*", "&")):
        normalized = normalized[1:]
    return normalized


def type_names_compatible(expected: str, actual: str) -> bool:
    """Require exact normalized identity; suffix guessing is not type proof."""
    left = normalize_type_name(expected)
    right = normalize_type_name(actual)
    return bool(left and right and left == right)


def _evidence_order(item: TypeEvidence) -> tuple[str, str, int, str, str]:
    return (
        item.operation_id,
        item.role.value,
        item.position,
        item.normalized_type,
        item.evidence_id,
    )
