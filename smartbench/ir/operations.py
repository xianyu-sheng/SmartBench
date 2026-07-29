"""Language-neutral operations and control-flow relations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from smartbench.ir.evidence import EvidenceRef


class OperationKind(str, Enum):
    """Finite semantic operations produced by language frontends."""

    FUNCTION = "function"
    PARAMETER = "parameter"
    ASSIGN = "assign"
    UPDATE = "update"
    CALL = "call"
    BRANCH = "branch"
    LOOP = "loop"
    RETURN = "return"
    CONTINUE = "continue"
    BREAK = "break"
    DEFER = "defer"
    SPAWN = "spawn"
    SEND = "send"
    RECEIVE = "receive"
    SELECT = "select"
    EMIT = "emit"
    UNKNOWN = "unknown"


class OperationEdgeKind(str, Enum):
    """Relations between normalized operations."""

    CONTAINS = "contains"
    NEXT = "next"
    TRUE_BRANCH = "true_branch"
    FALSE_BRANCH = "false_branch"
    BODY = "body"
    LOOP_BACK = "loop_back"
    LOOP_EXIT = "loop_exit"
    CALLS = "calls"
    DATA_DEPENDENCY = "data_dependency"
    SYNCHRONIZES = "synchronizes"


class DataFlowKind(str, Enum):
    """Stable meanings carried by ``DATA_DEPENDENCY`` edge attributes."""

    ARGUMENT_TO_PARAMETER = "argument_to_parameter"
    RETURN_TO_CALL = "return_to_call"


@dataclass(frozen=True)
class SemanticOperation:
    """One normalized operation with precise source provenance.

    Frontends use a small shared attribute contract for interprocedural data:

    - ``FUNCTION``: ``qualified_name``, ``namespace``, ``receiver_type`` and
      ``return_types``;
    - ``PARAMETER``: ``position``, ``declared_type``, ``parameter_kind`` and
      ``receiver``;
    - ``ASSIGN``: aligned ``bindings`` entries containing target and type;
    - ``CALL``/``SPAWN``/``DEFER``: ``arguments``, ``argument_names``,
      ``receiver`` and ``result_targets``;
    - ``RETURN``: ``values``.

    Missing type information is represented by an empty string.  An analyzer
    must never interpret a missing attribute as proof of a type.
    """

    id: str
    kind: OperationKind
    language: str
    scope_id: str
    location: EvidenceRef
    target: str = ""
    value: str = ""
    operands: tuple[str, ...] = field(default_factory=tuple)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_id(
        file_path: str,
        line_start: int,
        kind: OperationKind | str,
        ordinal: int = 0,
    ) -> str:
        normalized = kind.value if isinstance(kind, OperationKind) else kind
        raw = f"{file_path}:{line_start}:{normalized}:{ordinal}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "language": self.language,
            "scope_id": self.scope_id,
            "location": self.location.to_dict(),
            "target": self.target,
            "value": self.value,
            "operands": list(self.operands),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class OperationEdge:
    """A deterministic control/data relation between operations."""

    source_id: str
    target_id: str
    kind: OperationEdgeKind
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind.value,
            "attributes": dict(self.attributes),
        }
