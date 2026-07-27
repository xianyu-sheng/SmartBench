"""Language-neutral control-flow graph algorithms over SemanticIR operations."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from smartbench.ir import (
    OperationEdge,
    OperationEdgeKind,
    SemanticIR,
    SemanticOperation,
)

CONTROL_FLOW_EDGE_KINDS = frozenset({
    OperationEdgeKind.NEXT,
    OperationEdgeKind.TRUE_BRANCH,
    OperationEdgeKind.FALSE_BRANCH,
    OperationEdgeKind.BODY,
    OperationEdgeKind.LOOP_BACK,
    OperationEdgeKind.LOOP_EXIT,
})


@dataclass(frozen=True)
class ControlFlowArc:
    """One typed edge in an intraprocedural control-flow graph."""

    source_id: str
    target_id: str
    kind: OperationEdgeKind


class ControlFlowGraph:
    """Deterministic reachability, dominance, and branch-control queries."""

    def __init__(
        self,
        operations: Iterable[SemanticOperation],
        edges: Iterable[OperationEdge],
    ) -> None:
        self.operations = {operation.id: operation for operation in operations}
        self._successors: dict[str, list[ControlFlowArc]] = defaultdict(list)
        self._predecessors: dict[str, list[ControlFlowArc]] = defaultdict(list)
        for edge in edges:
            if edge.kind not in CONTROL_FLOW_EDGE_KINDS:
                continue
            source = self.operations.get(edge.source_id)
            target = self.operations.get(edge.target_id)
            if source is None or target is None or source.scope_id != target.scope_id:
                continue
            arc = ControlFlowArc(edge.source_id, edge.target_id, edge.kind)
            self._successors[edge.source_id].append(arc)
            self._predecessors[edge.target_id].append(arc)
        for arcs in [*self._successors.values(), *self._predecessors.values()]:
            arcs.sort(key=lambda arc: (arc.kind.value, arc.target_id, arc.source_id))
        self._reachability_cache: dict[tuple[str, str], bool] = {}
        self._dominators_by_scope: dict[str, dict[str, frozenset[str]]] = {}

    @classmethod
    def from_ir(
        cls,
        ir: SemanticIR,
        languages: Iterable[str] | None = None,
    ) -> "ControlFlowGraph":
        language_filter = {language.lower() for language in languages or ()}
        operations = [
            operation for operation in ir.operations
            if not language_filter or operation.language.lower() in language_filter
        ]
        operation_ids = {operation.id for operation in operations}
        edges = [
            edge for edge in ir.operation_edges
            if edge.source_id in operation_ids and edge.target_id in operation_ids
        ]
        return cls(operations, edges)

    def successors(
        self,
        operation_id: str,
        kinds: Iterable[OperationEdgeKind] | None = None,
    ) -> tuple[ControlFlowArc, ...]:
        kind_filter = set(kinds or ())
        return tuple(
            arc for arc in self._successors.get(operation_id, ())
            if not kind_filter or arc.kind in kind_filter
        )

    def predecessors(self, operation_id: str) -> tuple[ControlFlowArc, ...]:
        return tuple(self._predecessors.get(operation_id, ()))

    def reachable(self, source_id: str, target_id: str) -> bool:
        """Return whether a non-empty CFG path connects two operations."""
        key = (source_id, target_id)
        cached = self._reachability_cache.get(key)
        if cached is not None:
            return cached
        source = self.operations.get(source_id)
        target = self.operations.get(target_id)
        if source is None or target is None or source.scope_id != target.scope_id:
            self._reachability_cache[key] = False
            return False
        queue = deque(arc.target_id for arc in self._successors.get(source_id, ()))
        visited: set[str] = set()
        while queue:
            current = queue.popleft()
            if current == target_id:
                self._reachability_cache[key] = True
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(
                arc.target_id for arc in self._successors.get(current, ())
                if arc.target_id not in visited
            )
        self._reachability_cache[key] = False
        return False

    def dominates(self, dominator_id: str, operation_id: str) -> bool:
        """Return whether every entry-to-operation path contains ``dominator``."""
        operation = self.operations.get(operation_id)
        dominator = self.operations.get(dominator_id)
        if operation is None or dominator is None or operation.scope_id != dominator.scope_id:
            return False
        scope_dominators = self._dominators(operation.scope_id)
        return dominator_id in scope_dominators.get(operation_id, frozenset())

    def dominates_between(self, start_id: str, dominator_id: str, operation_id: str) -> bool:
        """Return whether every path from ``start_id`` to ``operation_id`` visits ``dominator_id``.

        Global dominance is often too strong for temporal invariants: an event
        may occur only on one conditional path while the action is also
        reachable from unrelated paths.  This query keeps the proof scoped to
        paths that actually begin at the matched event.
        """
        start = self.operations.get(start_id)
        dominator = self.operations.get(dominator_id)
        target = self.operations.get(operation_id)
        if (
            start is None
            or dominator is None
            or target is None
            or len({start.scope_id, dominator.scope_id, target.scope_id}) != 1
        ):
            return False
        if start_id == dominator_id:
            return True
        if operation_id == dominator_id:
            return bool(self.reachable(start_id, operation_id))

        # Look for a counterexample path that reaches the target without
        # visiting the proposed dominator.
        queue = deque(
            arc.target_id
            for arc in self._successors.get(start_id, ())
            if arc.target_id != dominator_id
        )
        visited: set[str] = set()
        while queue:
            current = queue.popleft()
            if current == dominator_id or current in visited:
                continue
            if current == operation_id:
                return False
            visited.add(current)
            queue.extend(
                arc.target_id
                for arc in self._successors.get(current, ())
                if arc.target_id != dominator_id and arc.target_id not in visited
            )
        return True

    def branch_controls(self, branch_id: str, operation_id: str) -> bool:
        """Return whether exactly one branch outcome can reach an operation.

        This excludes guards whose true and false outcomes both converge on the
        action, a common source-order false positive.
        """
        outcomes: list[bool] = []
        for kind in (OperationEdgeKind.TRUE_BRANCH, OperationEdgeKind.FALSE_BRANCH):
            targets = [arc.target_id for arc in self.successors(branch_id, {kind})]
            if not targets:
                continue
            outcomes.append(any(
                target == operation_id
                or self._reachable_without(
                    target,
                    operation_id,
                    forbidden={OperationEdgeKind.LOOP_BACK},
                )
                for target in targets
            ))
        return len(outcomes) == 2 and sum(outcomes) == 1

    def _reachable_without(
        self,
        source_id: str,
        target_id: str,
        forbidden: set[OperationEdgeKind],
    ) -> bool:
        """Reachability with selected structural edges disabled."""
        if source_id == target_id:
            return True
        queue = deque([source_id])
        visited: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for arc in self._successors.get(current, ()):
                if arc.kind in forbidden:
                    continue
                if arc.target_id == target_id:
                    return True
                queue.append(arc.target_id)
        return False

    def _dominators(self, scope_id: str) -> dict[str, frozenset[str]]:
        cached = self._dominators_by_scope.get(scope_id)
        if cached is not None:
            return cached
        nodes = {
            operation.id for operation in self.operations.values()
            if operation.scope_id == scope_id
        }
        if not nodes:
            self._dominators_by_scope[scope_id] = {}
            return {}
        entries = {
            node for node in nodes
            if not any(arc.source_id in nodes for arc in self._predecessors.get(node, ()))
        }
        if not entries:
            entries = {min(nodes)}
        dominators: dict[str, set[str]] = {
            node: ({node} if node in entries else set(nodes))
            for node in nodes
        }
        changed = True
        while changed:
            changed = False
            for node in sorted(nodes - entries):
                predecessors = [
                    arc.source_id for arc in self._predecessors.get(node, ())
                    if arc.source_id in nodes
                ]
                if predecessors:
                    common = set.intersection(*(dominators[item] for item in predecessors))
                    updated = {node, *common}
                else:
                    updated = {node}
                if updated != dominators[node]:
                    dominators[node] = updated
                    changed = True
        frozen = {node: frozenset(values) for node, values in dominators.items()}
        self._dominators_by_scope[scope_id] = frozen
        return frozen
