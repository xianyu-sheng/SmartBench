"""Bounded interprocedural control-flow queries over SemanticIR."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum

from smartbench.ir import (
    OperationEdgeKind,
    OperationKind,
    SemanticIR,
    SemanticOperation,
)

_CONTROL_FLOW_EDGE_KINDS = frozenset(
    {
        OperationEdgeKind.NEXT,
        OperationEdgeKind.TRUE_BRANCH,
        OperationEdgeKind.FALSE_BRANCH,
        OperationEdgeKind.BODY,
        OperationEdgeKind.LOOP_BACK,
        OperationEdgeKind.LOOP_EXIT,
    }
)
_CALL_KINDS = frozenset({OperationKind.CALL})
_MAX_CALL_DEPTH = 16
_MAX_STEPS = 100_000


class ICFGArcKind(str, Enum):
    """Kinds of arcs exposed by the bounded ICFG view."""

    CONTROL_FLOW = "control_flow"
    EMBEDDED_CALL = "embedded_call"
    CALL_ENTER = "call_enter"
    CALL_RETURN = "call_return"
    FUNCTION_ENTRY = "function_entry"


@dataclass(frozen=True)
class ICFGArc:
    """One ICFG transition with its originating semantic edge."""

    source_id: str
    target_id: str
    kind: ICFGArcKind
    call_id: str = ""
    attributes: dict[str, object] | None = None


@dataclass(frozen=True)
class ICFGPath:
    """A deterministic operation path returned by an ICFG query."""

    operation_ids: tuple[str, ...]
    arc_kinds: tuple[ICFGArcKind, ...]
    call_depth: int

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_ids": list(self.operation_ids),
            "arc_kinds": [kind.value for kind in self.arc_kinds],
            "call_depth": self.call_depth,
        }


@dataclass(frozen=True)
class _CallFrame:
    call_id: str
    callee_scope: str
    continuations: tuple[str, ...]


class InterproceduralControlFlowGraph:
    """A conservative, context-sensitive and depth-bounded ICFG.

    The graph uses existing intraprocedural CFG edges and linked ``CALLS``
    edges.  Embedded calls (for example ``value = helper()``) are connected to
    their host operation through the frontend's ``host_operation`` attribute.
    Only ordinary ``CALL`` operations enter the synchronous call stack by
    default; goroutines and deferred calls remain semantic relations rather
    than ordinary control-flow edges.
    """

    def __init__(
        self,
        ir: SemanticIR,
        *,
        include_async: bool = False,
    ) -> None:
        self.operations = {operation.id: operation for operation in ir.operations}
        self._successors: dict[str, list[ICFGArc]] = defaultdict(list)
        self._entries: dict[str, str] = {}
        self._host_continuations: dict[str, list[str]] = defaultdict(list)
        self._call_targets: dict[str, tuple[str, ...]] = defaultdict(tuple)
        self._call_hosts: dict[str, str] = {}
        self._build(ir, include_async=include_async)

    def successors(self, operation_id: str) -> tuple[ICFGArc, ...]:
        """Return structural ICFG arcs without executing a path query."""
        return tuple(self._successors.get(operation_id, ()))

    def function_entry(self, function_id: str) -> str | None:
        return self._entries.get(function_id)

    def calls_to(self, function_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                call_id for call_id, targets in self._call_targets.items() if function_id in targets
            )
        )

    def path(
        self,
        source_id: str,
        target_id: str,
        *,
        max_call_depth: int = 4,
        max_steps: int = 10_000,
    ) -> ICFGPath | None:
        """Return the shortest bounded path, or ``None`` if unproven."""
        if not 0 <= max_call_depth <= _MAX_CALL_DEPTH:
            raise ValueError(f"max_call_depth must be between 0 and {_MAX_CALL_DEPTH}")
        if not 1 <= max_steps <= _MAX_STEPS:
            raise ValueError(f"max_steps must be between 1 and {_MAX_STEPS}")
        if source_id not in self.operations or target_id not in self.operations:
            return None
        if source_id == target_id:
            return ICFGPath((source_id,), (), 0)

        queue: deque[
            tuple[str, tuple[_CallFrame, ...], tuple[str, ...], tuple[ICFGArcKind, ...]]
        ] = deque([(source_id, (), (source_id,), ())])
        visited: set[tuple[str, tuple[_CallFrame, ...]]] = set()
        steps = 0
        while queue and steps < max_steps:
            current, stack, operation_path, arc_path = queue.popleft()
            state = (current, stack)
            if state in visited:
                continue
            visited.add(state)
            for arc in self._successors.get(current, ()):
                next_stack = stack
                if arc.kind == ICFGArcKind.CALL_ENTER:
                    if len(stack) >= max_call_depth:
                        continue
                    targets = self._call_targets.get(arc.call_id, ())
                    callee_scope = arc.target_id
                    if callee_scope not in targets:
                        continue
                    continuations = self._continuations(arc.call_id)
                    next_stack = (*stack, _CallFrame(arc.call_id, callee_scope, continuations))
                elif arc.kind == ICFGArcKind.CALL_RETURN:
                    if (
                        not stack
                        or arc.call_id != stack[-1].call_id
                        or stack[-1].callee_scope != current_scope(self.operations, current)
                        or arc.target_id not in stack[-1].continuations
                    ):
                        continue
                    next_stack = stack[:-1]
                next_path = (*operation_path, arc.target_id)
                next_arcs = (*arc_path, arc.kind)
                steps += 1
                if arc.target_id == target_id:
                    return ICFGPath(next_path, next_arcs, len(next_stack))
                queue.append((arc.target_id, next_stack, next_path, next_arcs))
        return None

    def reachable(
        self,
        source_id: str,
        target_id: str,
        *,
        max_call_depth: int = 4,
        max_steps: int = 10_000,
    ) -> bool:
        """Return whether a bounded, stack-consistent path is proven."""
        return (
            self.path(
                source_id,
                target_id,
                max_call_depth=max_call_depth,
                max_steps=max_steps,
            )
            is not None
        )

    def _build(self, ir: SemanticIR, *, include_async: bool) -> None:
        operations = sorted(ir.operations, key=_operation_order)
        for operation in operations:
            if operation.kind in _CALL_KINDS or (
                include_async and operation.kind in {OperationKind.SPAWN, OperationKind.DEFER}
            ):
                self._call_hosts[operation.id] = str(operation.attributes.get("host_operation", ""))

        by_id = self.operations
        for edge in ir.operation_edges:
            source = by_id.get(edge.source_id)
            target = by_id.get(edge.target_id)
            if source is None or target is None:
                continue
            if edge.kind in _CONTROL_FLOW_EDGE_KINDS and source.scope_id == target.scope_id:
                self._host_continuations[source.id].append(target.id)
                if source.id in self._call_hosts.values():
                    # An embedded call owns the host's continuation.  The
                    # continuation is reintroduced by CALL_RETURN, so a
                    # direct host->next arc would incorrectly bypass callee
                    # execution.
                    continue
                self._successors[source.id].append(
                    ICFGArc(source.id, target.id, ICFGArcKind.CONTROL_FLOW)
                )
            if edge.kind == OperationEdgeKind.CONTAINS:
                if source.kind == OperationKind.FUNCTION and target.kind != OperationKind.PARAMETER:
                    self._entries.setdefault(source.id, target.id)
            if edge.kind == OperationEdgeKind.CALLS:
                call = source
                if call.kind not in _CALL_KINDS and not (
                    include_async and call.kind in {OperationKind.SPAWN, OperationKind.DEFER}
                ):
                    continue
                current = list(self._call_targets.get(call.id, ()))
                if target.kind == OperationKind.FUNCTION and target.id not in current:
                    current.append(target.id)
                    self._call_targets[call.id] = tuple(sorted(current))

        for operation in operations:
            if operation.kind == OperationKind.FUNCTION:
                self._entries.setdefault(operation.id, operation.id)
                self._successors[operation.id].append(
                    ICFGArc(
                        operation.id,
                        self._entries[operation.id],
                        ICFGArcKind.FUNCTION_ENTRY,
                    )
                )

        for call_id, host_id in sorted(self._call_hosts.items()):
            if not host_id or host_id not in by_id:
                continue
            self._successors[host_id].append(
                ICFGArc(host_id, call_id, ICFGArcKind.EMBEDDED_CALL, call_id=call_id)
            )
        for call_id, targets in sorted(self._call_targets.items()):
            for target_id in targets:
                entry = self._entries.get(target_id)
                if entry is None:
                    continue
                self._successors[call_id].append(
                    ICFGArc(call_id, target_id, ICFGArcKind.CALL_ENTER, call_id=call_id)
                )

        for call_id in sorted(self._call_targets):
            for return_id, operation in self.operations.items():
                if operation.kind != OperationKind.RETURN:
                    continue
                if operation.scope_id not in self._call_targets[call_id]:
                    continue
                # Return arcs are filtered by the stack frame during traversal.
                for continuation in self._continuations(call_id):
                    self._successors[return_id].append(
                        ICFGArc(
                            return_id,
                            continuation,
                            ICFGArcKind.CALL_RETURN,
                            call_id=call_id,
                        )
                    )

        for arcs in self._successors.values():
            arcs.sort(key=lambda arc: (arc.target_id, arc.kind.value, arc.call_id))

    def _continuations(self, call_id: str) -> tuple[str, ...]:
        host_id = self._call_hosts.get(call_id, "") or call_id
        return tuple(sorted(set(self._host_continuations.get(host_id, ()))))


def current_scope(
    operations: dict[str, SemanticOperation],
    operation_id: str,
) -> str:
    return operations[operation_id].scope_id


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, str]:
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        operation.id,
    )
