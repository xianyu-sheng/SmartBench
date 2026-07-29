"""Declarative state-machine invariants over normalized operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from smartbench.analysis.control_flow import ControlFlowGraph
from smartbench.analysis.icfg import ICFGPath, InterproceduralControlFlowGraph
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


class StateScope(str, Enum):
    """Proof scope explicitly requested by a state invariant."""

    INTRAPROCEDURAL = "intraprocedural"
    INTERPROCEDURAL = "interprocedural"


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
    scope: StateScope = StateScope.INTRAPROCEDURAL
    max_call_depth: int = 4

    def __post_init__(self) -> None:
        if self.kind == InvariantKind.REQUIRE_GUARD_BEFORE_ACTION and self.guard is None:
            raise ValueError("guard is required for require_guard_before_action")
        if not isinstance(self.scope, StateScope):
            try:
                object.__setattr__(self, "scope", StateScope(self.scope))
            except ValueError as exc:
                raise ValueError("scope must be intraprocedural or interprocedural") from exc
        if (
            isinstance(self.max_call_depth, bool)
            or not isinstance(self.max_call_depth, int)
            or not 0 <= self.max_call_depth <= 16
        ):
            raise ValueError("max_call_depth must be between 0 and 16")


@dataclass(frozen=True)
class StateInvariantViolation:
    invariant_id: str
    message: str
    scope_id: str
    event: SemanticOperation
    action: SemanticOperation
    missing: str = ""
    path: ICFGPath | None = None
    path_operations: tuple[SemanticOperation, ...] = ()

    def to_fact(self) -> SemanticFact:
        attributes = {
            "invariant_id": self.invariant_id,
            "event_operation": self.event.id,
            "action_operation": self.action.id,
            "missing": self.missing,
        }
        evidence = [self.event.location, self.action.location]
        seen_evidence = {
            (reference.file_path, reference.line_start, reference.line_end)
            for reference in evidence
        }
        for operation in self.path_operations:
            reference = operation.location
            key = (reference.file_path, reference.line_start, reference.line_end)
            if key not in seen_evidence:
                evidence.append(reference)
                seen_evidence.add(key)
            if len(evidence) >= 8:
                break
        if self.path is not None:
            attributes.update(
                {
                    "proof_scope": StateScope.INTERPROCEDURAL.value,
                    "path_operations": list(self.path.operation_ids),
                    "path_arcs": [kind.value for kind in self.path.arc_kinds],
                    "call_depth": self.path.call_depth,
                }
            )
        return SemanticFact(
            subject=self.scope_id,
            predicate=FactKind.STATE_TRANSITION,
            object=self.message,
            evidence=tuple(evidence),
            confidence=1.0,
            attributes=attributes,
        )


@dataclass
class StateAnalysisResult:
    violations: list[StateInvariantViolation] = field(default_factory=list)
    invariants_evaluated: int = 0
    scopes_evaluated: int = 0
    interprocedural_paths: int = 0
    interprocedural_unknowns: int = 0

    def to_evidence_pack(self, query: str, graph_version: str = "") -> EvidencePack:
        return EvidencePack.from_facts(
            query,
            [violation.to_fact() for violation in self.violations],
            retrieval_trace=(
                f"state-invariants:{self.invariants_evaluated}",
                f"state-scopes:{self.scopes_evaluated}",
                f"state-interprocedural-paths:{self.interprocedural_paths}",
                f"state-interprocedural-unknowns:{self.interprocedural_unknowns}",
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
        icfg = (
            InterproceduralControlFlowGraph(ir)
            if any(invariant.scope == StateScope.INTERPROCEDURAL for invariant in invariant_list)
            else None
        )
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
                if invariant.scope == StateScope.INTERPROCEDURAL:
                    continue
                result.violations.extend(self._evaluate_scope(scope_id, operations, invariant, cfg))
        if icfg is not None:
            all_operations = sorted(
                [operation for operations in by_scope.values() for operation in operations],
                key=self._order_key,
            )
            for invariant in invariant_list:
                if invariant.scope != StateScope.INTERPROCEDURAL:
                    continue
                violations, paths, unknowns = self._evaluate_interprocedural(
                    all_operations,
                    invariant,
                    cfg,
                    icfg,
                )
                result.violations.extend(violations)
                result.interprocedural_paths += paths
                result.interprocedural_unknowns += unknowns
        return result

    def _evaluate_interprocedural(
        self,
        operations: list[SemanticOperation],
        invariant: StateInvariant,
        cfg: ControlFlowGraph,
        icfg: InterproceduralControlFlowGraph,
    ) -> tuple[list[StateInvariantViolation], int, int]:
        events = [operation for operation in operations if invariant.event.matches(operation)]
        actions = [operation for operation in operations if invariant.action.matches(operation)]
        violations: list[StateInvariantViolation] = []
        paths_evaluated = 0
        unknowns = 0
        for event in events:
            for action in actions:
                if event.scope_id == action.scope_id or event.id == action.id:
                    continue
                path = icfg.path(
                    event.id,
                    action.id,
                    max_call_depth=invariant.max_call_depth,
                )
                if path is None:
                    continue
                paths_evaluated += 1
                path_operations = {
                    operation.id: operation
                    for operation in operations
                    if operation.id in path.operation_ids
                }
                if invariant.kind == InvariantKind.REQUIRE_GUARD_BEFORE_ACTION:
                    guards = [
                        path_operations[operation_id]
                        for operation_id in path.operation_ids
                        if operation_id in path_operations
                        and operation_id not in {event.id, action.id}
                        and invariant.guard is not None
                        and invariant.guard.matches(path_operations[operation_id])
                    ]
                    if not guards:
                        violations.append(
                            StateInvariantViolation(
                                invariant_id=invariant.invariant_id,
                                message=invariant.message,
                                scope_id=event.scope_id,
                                event=event,
                                action=action,
                                missing="guard",
                                path=path,
                                path_operations=tuple(
                                    path_operations[operation_id]
                                    for operation_id in path.operation_ids
                                    if operation_id in path_operations
                                ),
                            )
                        )
                    elif not any(
                        self._cross_scope_guard_proves_action(guard, action, cfg)
                        for guard in guards
                    ):
                        # A guard exists, but this proof policy does not claim
                        # that a caller-side or non-controlling guard protects
                        # the action.  Preserve soundness by abstaining.
                        unknowns += 1
                elif invariant.kind in {
                    InvariantKind.FORBID_ACTION_AFTER_EVENT,
                    InvariantKind.REQUIRE_EXIT_AFTER_EVENT,
                }:
                    violations.append(
                        StateInvariantViolation(
                            invariant_id=invariant.invariant_id,
                            message=invariant.message,
                            scope_id=event.scope_id,
                            event=event,
                            action=action,
                            missing=(
                                "forbidden_action"
                                if invariant.kind == InvariantKind.FORBID_ACTION_AFTER_EVENT
                                else "exit"
                            ),
                            path=path,
                            path_operations=tuple(
                                path_operations[operation_id]
                                for operation_id in path.operation_ids
                                if operation_id in path_operations
                            ),
                        )
                    )
        return violations, paths_evaluated, unknowns

    @staticmethod
    def _cross_scope_guard_proves_action(
        guard: SemanticOperation,
        action: SemanticOperation,
        cfg: ControlFlowGraph,
    ) -> bool:
        if guard.scope_id != action.scope_id:
            return False
        action_point = str(action.attributes.get("host_operation", "")) or action.id
        return (
            cfg.reachable(guard.id, action_point)
            and cfg.dominates(guard.id, action_point)
            and cfg.branch_controls(guard.id, action_point)
        )

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
                action
                for action in actions
                if action.id != event.id and cfg.reachable(event.id, action.id)
            ]
            for action in later_actions:
                if invariant.kind == InvariantKind.REQUIRE_GUARD_BEFORE_ACTION:
                    assert invariant.guard is not None
                    guards = [
                        operation
                        for operation in operations
                        if operation.id not in {event.id, action.id}
                        and invariant.guard.matches(operation)
                        and cfg.reachable(event.id, operation.id)
                        and cfg.reachable(operation.id, action.id)
                        and cfg.dominates_between(event.id, operation.id, action.id)
                        and self._guard_proves_action(event, operation, action, cfg)
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
    def _guard_proves_action(
        event: SemanticOperation,
        guard: SemanticOperation,
        action: SemanticOperation,
        cfg: ControlFlowGraph,
    ) -> bool:
        """Check whether a matched guard establishes protection for an action.

        Branch guards need an exclusive outcome that controls the action.  A
        regular semantic operation (for example, registering a cleanup,
        acquiring a lock, or setting a transaction marker) is proven by
        dominance alone: it must execute on every path from the matched event
        to the action.  Keeping this distinction in the language-neutral
        analyzer lets frontends expose either shape through the same IR.
        """
        if guard.kind == OperationKind.BRANCH:
            return cfg.branch_controls(guard.id, action.id)
        return cfg.dominates_between(event.id, guard.id, action.id)

    @staticmethod
    def _order_key(operation: SemanticOperation) -> tuple[str, int, int, str]:
        return (
            operation.location.file_path,
            operation.location.line_start,
            operation.location.column_start or 0,
            operation.id,
        )
