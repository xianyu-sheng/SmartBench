"""Declarative state-machine invariants over normalized operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from smartbench.analysis.control_flow import ControlFlowGraph
from smartbench.ir import (
    EvidencePack,
    FactKind,
    OperationKind,
    SemanticFact,
    SemanticIR,
    SemanticOperation,
)


class InvariantKind(str, Enum):
    """Generic temporal relations understood by the analyzer."""

    REQUIRE_GUARD_BEFORE_ACTION = "require_guard_before_action"
    FORBID_ACTION_AFTER_EVENT = "forbid_action_after_event"
    REQUIRE_EXIT_AFTER_EVENT = "require_exit_after_event"


@dataclass(frozen=True)
class OperationSelector:
    """Language-neutral selector for normalized operations."""

    kinds: frozenset[OperationKind] = field(default_factory=frozenset)
    contains_all: tuple[str, ...] = field(default_factory=tuple)
    contains_any: tuple[str, ...] = field(default_factory=tuple)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        *kinds: OperationKind,
        contains_all: Iterable[str] = (),
        contains_any: Iterable[str] = (),
        attributes: Mapping[str, Any] | None = None,
    ) -> "OperationSelector":
        return cls(
            kinds=frozenset(kinds),
            contains_all=tuple(contains_all),
            contains_any=tuple(contains_any),
            attributes=attributes or {},
        )

    def matches(self, operation: SemanticOperation) -> bool:
        if self.kinds and operation.kind not in self.kinds:
            return False
        searchable = " ".join(
            [
                operation.target,
                operation.value,
                " ".join(operation.operands),
                json.dumps(dict(operation.attributes), ensure_ascii=False, sort_keys=True),
            ]
        ).lower()
        if any(token.lower() not in searchable for token in self.contains_all):
            return False
        if self.contains_any and not any(
            token.lower() in searchable for token in self.contains_any
        ):
            return False
        for key, expected in self.attributes.items():
            if operation.attributes.get(key) != expected:
                return False
        return True


@dataclass(frozen=True)
class StateInvariant:
    """A reusable temporal invariant supplied by a rule or benchmark."""

    invariant_id: str
    kind: InvariantKind
    event: OperationSelector
    action: OperationSelector
    guard: OperationSelector | None = None
    exits: OperationSelector = field(
        default_factory=lambda: OperationSelector.of(
            OperationKind.RETURN,
            OperationKind.BREAK,
        )
    )
    message: str = "state-machine invariant violated"

    def __post_init__(self) -> None:
        if self.kind == InvariantKind.REQUIRE_GUARD_BEFORE_ACTION and self.guard is None:
            raise ValueError("guard is required for require_guard_before_action")


@dataclass(frozen=True)
class StateInvariantViolation:
    invariant_id: str
    message: str
    scope_id: str
    event: SemanticOperation
    action: SemanticOperation
    missing: str = ""

    def to_fact(self) -> SemanticFact:
        return SemanticFact(
            subject=self.scope_id,
            predicate=FactKind.STATE_TRANSITION,
            object=self.message,
            evidence=(self.event.location, self.action.location),
            confidence=1.0,
            attributes={
                "invariant_id": self.invariant_id,
                "event_operation": self.event.id,
                "action_operation": self.action.id,
                "missing": self.missing,
            },
        )


@dataclass
class StateAnalysisResult:
    violations: list[StateInvariantViolation] = field(default_factory=list)
    invariants_evaluated: int = 0
    scopes_evaluated: int = 0

    def to_evidence_pack(self, query: str, graph_version: str = "") -> EvidencePack:
        return EvidencePack.from_facts(
            query,
            [violation.to_fact() for violation in self.violations],
            retrieval_trace=(
                f"state-invariants:{self.invariants_evaluated}",
                f"state-scopes:{self.scopes_evaluated}",
            ),
            graph_version=graph_version,
        )


class StateMachineAnalyzer:
    """Evaluate declarative invariants without language-specific syntax."""

    def analyze(
        self,
        ir: SemanticIR,
        invariants: Iterable[StateInvariant],
        languages: Iterable[str] | None = None,
    ) -> StateAnalysisResult:
        invariant_list = list(invariants)
        language_filter = {language.lower() for language in languages or ()}
        cfg = ControlFlowGraph.from_ir(ir, language_filter)
        by_scope: dict[str, list[SemanticOperation]] = {}
        for operation in ir.operations:
            if language_filter and operation.language.lower() not in language_filter:
                continue
            if not operation.scope_id:
                continue
            by_scope.setdefault(operation.scope_id, []).append(operation)
        for operations in by_scope.values():
            operations.sort(key=self._order_key)

        result = StateAnalysisResult(
            invariants_evaluated=len(invariant_list),
            scopes_evaluated=len(by_scope),
        )
        for scope_id, operations in sorted(by_scope.items()):
            for invariant in invariant_list:
                result.violations.extend(
                    self._evaluate_scope(scope_id, operations, invariant, cfg)
                )
        return result

    def _evaluate_scope(
        self,
        scope_id: str,
        operations: list[SemanticOperation],
        invariant: StateInvariant,
        cfg: ControlFlowGraph,
    ) -> list[StateInvariantViolation]:
        events = [operation for operation in operations if invariant.event.matches(operation)]
        actions = [operation for operation in operations if invariant.action.matches(operation)]
        violations: list[StateInvariantViolation] = []

        for event in events:
            later_actions = [
                action for action in actions
                if action.id != event.id and cfg.reachable(event.id, action.id)
            ]
            for action in later_actions:
                if invariant.kind == InvariantKind.REQUIRE_GUARD_BEFORE_ACTION:
                    assert invariant.guard is not None
                    guards = [
                        operation for operation in operations
                        if operation.id not in {event.id, action.id}
                        and invariant.guard.matches(operation)
                        and cfg.reachable(event.id, operation.id)
                        and cfg.reachable(operation.id, action.id)
                        and cfg.dominates(operation.id, action.id)
                        and cfg.branch_controls(operation.id, action.id)
                    ]
                    if guards:
                        continue
                    violations.append(
                        StateInvariantViolation(
                            invariant_id=invariant.invariant_id,
                            message=invariant.message,
                            scope_id=scope_id,
                            event=event,
                            action=action,
                            missing="guard",
                        )
                    )
                elif invariant.kind == InvariantKind.FORBID_ACTION_AFTER_EVENT:
                    violations.append(
                        StateInvariantViolation(
                            invariant_id=invariant.invariant_id,
                            message=invariant.message,
                            scope_id=scope_id,
                            event=event,
                            action=action,
                            missing="forbidden_action",
                        )
                    )
                elif invariant.kind == InvariantKind.REQUIRE_EXIT_AFTER_EVENT:
                    violations.append(
                        StateInvariantViolation(
                            invariant_id=invariant.invariant_id,
                            message=invariant.message,
                            scope_id=scope_id,
                            event=event,
                            action=action,
                            missing="exit",
                        )
                    )
        return violations

    @staticmethod
    def _order_key(operation: SemanticOperation) -> tuple[str, int, int, str]:
        return (
            operation.location.file_path,
            operation.location.line_start,
            operation.location.column_start or 0,
            operation.id,
        )
