"""Auditable cross-function state-path queries over the bounded ICFG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from smartbench.analysis.icfg import ICFGPath, InterproceduralControlFlowGraph
from smartbench.analysis.state_machine import OperationSelector
from smartbench.ir import FactKind, SemanticFact, SemanticIR, SemanticOperation


@dataclass(frozen=True)
class InterproceduralStatePath:
    """One proven event-to-action path crossing at least one function scope."""

    event: SemanticOperation
    action: SemanticOperation
    path: ICFGPath
    operations: tuple[SemanticOperation, ...]

    def to_fact(self) -> SemanticFact:
        evidence = tuple(operation.location for operation in self.operations[:8])
        return SemanticFact(
            subject=self.event.scope_id,
            predicate=FactKind.STATE_TRANSITION,
            object=self.action.target or self.action.value or self.action.kind.value,
            evidence=evidence,
            attributes={
                "link_kind": "interprocedural_state_path",
                "event_operation": self.event.id,
                "action_operation": self.action.id,
                "path_operations": list(self.path.operation_ids),
                "path_arcs": [kind.value for kind in self.path.arc_kinds],
                "call_depth": self.path.call_depth,
            },
        )


class InterproceduralStatePathQuery:
    """Find only bounded, source-backed event-to-action paths."""

    def find(
        self,
        ir: SemanticIR,
        event: OperationSelector,
        action: OperationSelector,
        *,
        languages: Iterable[str] | None = None,
        max_call_depth: int = 4,
        max_paths: int = 100,
    ) -> tuple[InterproceduralStatePath, ...]:
        if max_paths < 0:
            raise ValueError("max_paths must be non-negative")
        language_filter = {language.lower() for language in languages or ()}
        operations = [
            operation
            for operation in ir.operations
            if not language_filter or operation.language.lower() in language_filter
        ]
        events = sorted(
            (operation for operation in operations if event.matches(operation)),
            key=_operation_order,
        )
        actions = sorted(
            (operation for operation in operations if action.matches(operation)),
            key=_operation_order,
        )
        icfg = InterproceduralControlFlowGraph(ir)
        paths: list[InterproceduralStatePath] = []
        by_id = {operation.id: operation for operation in operations}
        for event_operation in events:
            for action_operation in actions:
                if event_operation.scope_id == action_operation.scope_id:
                    continue
                path = icfg.path(
                    event_operation.id,
                    action_operation.id,
                    max_call_depth=max_call_depth,
                )
                if path is None:
                    continue
                path_operations = tuple(
                    by_id[operation_id]
                    for operation_id in path.operation_ids
                    if operation_id in by_id
                )
                paths.append(
                    InterproceduralStatePath(
                        event=event_operation,
                        action=action_operation,
                        path=path,
                        operations=path_operations,
                    )
                )
                if len(paths) >= max_paths:
                    return tuple(paths)
        return tuple(paths)


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, str]:
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        operation.id,
    )
