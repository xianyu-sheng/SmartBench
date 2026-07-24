"""Conservative interprocedural call, data-flow, and synchronization linking."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from smartbench.ir import (
    DataFlowKind,
    FactKind,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticFact,
    SemanticIR,
    SemanticOperation,
)

_CALL_KINDS = frozenset({OperationKind.CALL, OperationKind.SPAWN, OperationKind.DEFER})
_MAX_CALL_DEPTH = 32


@dataclass
class SemanticLinkResult:
    """Edges and facts inferred without guessing ambiguous symbols."""

    edges: list[OperationEdge] = field(default_factory=list)
    facts: list[SemanticFact] = field(default_factory=list)
    resolved_calls: int = 0
    unresolved_calls: int = 0
    ambiguous_calls: int = 0
    typed_receiver_calls: int = 0
    argument_edges: int = 0
    return_edges: int = 0
    synchronization_edges: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "resolved_calls": self.resolved_calls,
            "unresolved_calls": self.unresolved_calls,
            "ambiguous_calls": self.ambiguous_calls,
            "typed_receiver_calls": self.typed_receiver_calls,
            "call_edges": sum(edge.kind == OperationEdgeKind.CALLS for edge in self.edges),
            "argument_edges": self.argument_edges,
            "return_edges": self.return_edges,
            "synchronization_edges": self.synchronization_edges,
        }


@dataclass
class _FunctionIndices:
    exact: dict[tuple[str, str], list[SemanticOperation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    by_namespace: dict[tuple[str, str], list[SemanticOperation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    by_simple: dict[tuple[str, str], list[SemanticOperation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    methods: dict[tuple[str, str, str, str], list[SemanticOperation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    methods_global: dict[tuple[str, str, str], list[SemanticOperation]] = field(
        default_factory=lambda: defaultdict(list)
    )


class SemanticLinker:
    """Resolve normalized operations across files using conservative symbol rules."""

    def link(self, ir: SemanticIR) -> SemanticLinkResult:
        result = SemanticLinkResult()
        operations = sorted(ir.operations, key=_operation_order)
        functions = [
            operation for operation in operations if operation.kind == OperationKind.FUNCTION
        ]
        by_id = {operation.id: operation for operation in operations}
        indices = self._function_indices(functions)
        parameters = self._operations_by_scope(operations, OperationKind.PARAMETER)
        returns = self._operations_by_scope(operations, OperationKind.RETURN)
        local_types = self._local_types(operations)
        calls = [operation for operation in operations if operation.kind in _CALL_KINDS]

        resolutions: dict[str, tuple[SemanticOperation, str]] = {}
        pending = list(calls)
        while pending:
            next_pending: list[SemanticOperation] = []
            type_progress = False
            for call in pending:
                caller = by_id.get(call.scope_id)
                candidates, resolution = self._resolve_call(
                    call,
                    caller,
                    indices,
                    local_types,
                )
                if len(candidates) != 1:
                    next_pending.append(call)
                    continue
                target = candidates[0]
                resolutions[call.id] = (target, resolution)
                type_progress |= self._propagate_return_types(call, target, local_types)
            if len(next_pending) == len(pending) and not type_progress:
                break
            pending = next_pending

        for call in calls:
            resolved = resolutions.get(call.id)
            if resolved is not None:
                target, resolution = resolved
                self._add_call_link(
                    call,
                    target,
                    resolution,
                    parameters.get(target.id, []),
                    returns.get(target.id, []),
                    result,
                )
                continue
            caller = by_id.get(call.scope_id)
            candidates, _ = self._resolve_call(call, caller, indices, local_types)
            if candidates:
                result.ambiguous_calls += 1
            else:
                result.unresolved_calls += 1

        self._link_synchronization(operations, result)
        return result

    @staticmethod
    def apply(ir: SemanticIR, result: SemanticLinkResult) -> None:
        """Merge a link result into an IR without duplicating deterministic edges."""
        known_edges = {_edge_key(edge) for edge in ir.operation_edges}
        for edge in result.edges:
            key = _edge_key(edge)
            if key not in known_edges:
                ir.operation_edges.append(edge)
                known_edges.add(key)
        known_facts = {fact.fact_id for fact in ir.facts}
        for fact in result.facts:
            if fact.fact_id not in known_facts:
                ir.facts.append(fact)
                known_facts.add(fact.fact_id)
        ir.meta["semantic_linker"] = result.to_dict()

    def _function_indices(
        self,
        functions: list[SemanticOperation],
    ) -> _FunctionIndices:
        indices = _FunctionIndices()
        for function in functions:
            language = function.language.lower()
            qualified = str(function.attributes.get("qualified_name", function.target))
            namespace = _normalize_symbol(str(function.attributes.get("namespace", "")))
            simple = str(function.attributes.get("symbol_name", function.target)).split(".")[-1]
            indices.exact[(language, _normalize_symbol(qualified))].append(function)
            if namespace:
                indices.by_namespace[(language, f"{namespace}::{simple}")].append(function)
            indices.by_simple[(language, simple)].append(function)
            receiver_type = _normalize_type(str(function.attributes.get("receiver_type", "")))
            if receiver_type:
                type_name = receiver_type.rsplit(".", 1)[-1]
                indices.methods[(language, namespace, type_name, simple)].append(function)
                indices.methods_global[(language, type_name, simple)].append(function)
        return indices

    def _resolve_call(
        self,
        call: SemanticOperation,
        caller: SemanticOperation | None,
        indices: _FunctionIndices,
        local_types: dict[str, dict[str, set[str]]],
    ) -> tuple[list[SemanticOperation], str]:
        language = call.language.lower()
        target = _normalize_symbol(call.target)
        if not target:
            return [], "empty"

        exact_candidates = indices.exact.get((language, target), [])
        if exact_candidates:
            return exact_candidates, "qualified"

        namespace = _normalize_symbol(str(caller.attributes.get("namespace", ""))) if caller else ""
        simple = target.split(".")[-1]
        if namespace:
            namespace_candidates = indices.by_namespace.get(
                (language, f"{namespace}::{simple}"),
                [],
            )
            if len(namespace_candidates) == 1 and "." not in target:
                return namespace_candidates, "namespace"

        contextual = self._contextual_method_target(target, caller)
        if contextual:
            contextual_candidates = indices.exact.get((language, contextual), [])
            if contextual_candidates:
                return contextual_candidates, "lexical_receiver"

        if "." in target:
            typed = self._typed_method_candidates(
                call,
                simple,
                namespace,
                indices,
                local_types,
            )
            if typed:
                return typed, "typed_receiver"
            return [], "unresolved_receiver"
        return indices.by_simple.get((language, simple), []), "unique_simple"

    @staticmethod
    def _typed_method_candidates(
        call: SemanticOperation,
        method_name: str,
        namespace: str,
        indices: _FunctionIndices,
        local_types: dict[str, dict[str, set[str]]],
    ) -> list[SemanticOperation]:
        receiver = _normalize_symbol(str(call.attributes.get("receiver", "")))
        if not receiver:
            receiver = _normalize_symbol(call.target).rsplit(".", 1)[0]
        receiver_types = local_types.get(call.scope_id, {}).get(receiver, set())
        normalized_types = {
            normalized
            for declared_type in receiver_types
            if (normalized := _normalize_type(declared_type))
        }
        if len(normalized_types) != 1:
            return []
        candidates: dict[str, SemanticOperation] = {}
        for normalized_type in normalized_types:
            exact_name = f"{normalized_type}.{method_name}"
            for candidate in indices.exact.get((call.language.lower(), exact_name), []):
                candidates[candidate.id] = candidate
            type_name = normalized_type.rsplit(".", 1)[-1]
            scoped = indices.methods.get(
                (call.language.lower(), namespace, type_name, method_name),
                [],
            )
            for candidate in scoped:
                candidates[candidate.id] = candidate
            if not scoped:
                for candidate in indices.methods_global.get(
                    (call.language.lower(), type_name, method_name),
                    [],
                ):
                    candidates[candidate.id] = candidate
        return sorted(candidates.values(), key=_operation_order)

    @staticmethod
    def _contextual_method_target(
        target: str,
        caller: SemanticOperation | None,
    ) -> str:
        if caller is None or not target.startswith(("self.", "cls.")):
            return ""
        qualified = _normalize_symbol(str(caller.attributes.get("qualified_name", "")))
        if qualified.count(".") < 2:
            return ""
        owner = qualified.rsplit(".", 1)[0]
        return f"{owner}.{target.split('.', 1)[1]}"

    @staticmethod
    def _operations_by_scope(
        operations: Iterable[SemanticOperation],
        kind: OperationKind,
    ) -> dict[str, list[SemanticOperation]]:
        grouped: dict[str, list[SemanticOperation]] = defaultdict(list)
        for operation in operations:
            if operation.kind == kind:
                grouped[operation.scope_id].append(operation)
        for values in grouped.values():
            values.sort(
                key=lambda operation: (
                    int(operation.attributes.get("position", 0)),
                    *_operation_order(operation),
                )
            )
        return grouped

    @staticmethod
    def _local_types(
        operations: Iterable[SemanticOperation],
    ) -> dict[str, dict[str, set[str]]]:
        local_types: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for operation in operations:
            if operation.kind == OperationKind.PARAMETER:
                declared = str(operation.attributes.get("declared_type", ""))
                if declared and operation.target:
                    local_types[operation.scope_id][operation.target].add(declared)
            elif operation.kind == OperationKind.ASSIGN:
                bindings = operation.attributes.get("bindings", [])
                if not isinstance(bindings, list):
                    continue
                for binding in bindings:
                    if not isinstance(binding, dict):
                        continue
                    target = str(binding.get("target", ""))
                    for key in ("declared_type", "inferred_type"):
                        value = str(binding.get(key, ""))
                        if target and value:
                            local_types[operation.scope_id][target].add(value)
        return local_types

    @staticmethod
    def _propagate_return_types(
        call: SemanticOperation,
        target: SemanticOperation,
        local_types: dict[str, dict[str, set[str]]],
    ) -> bool:
        result_targets = call.attributes.get("result_targets", [])
        return_types = target.attributes.get("return_types", [])
        if not isinstance(result_targets, list) or not isinstance(return_types, list):
            return False
        if not result_targets or not return_types:
            return False
        if len(return_types) == 1 and len(result_targets) != 1:
            return False
        progress = False
        for variable, return_type in zip(result_targets, return_types):
            variable_name = str(variable)
            type_name = str(return_type)
            if not variable_name or not _normalize_type(type_name):
                continue
            known = local_types[call.scope_id][variable_name]
            before = len(known)
            known.add(type_name)
            progress |= len(known) != before
        return progress

    def _add_call_link(
        self,
        call: SemanticOperation,
        target: SemanticOperation,
        resolution: str,
        parameters: list[SemanticOperation],
        returns: list[SemanticOperation],
        result: SemanticLinkResult,
    ) -> None:
        result.edges.append(
            OperationEdge(
                source_id=call.id,
                target_id=target.id,
                kind=OperationEdgeKind.CALLS,
                attributes={
                    "resolution": resolution,
                    "call_kind": call.kind.value,
                },
            )
        )
        result.facts.append(self._call_fact(call, target, resolution))
        result.resolved_calls += 1
        if resolution == "typed_receiver":
            result.typed_receiver_calls += 1
        self._link_arguments(call, target, parameters, result)
        for return_operation in returns:
            edge = OperationEdge(
                source_id=return_operation.id,
                target_id=call.id,
                kind=OperationEdgeKind.DATA_DEPENDENCY,
                attributes={
                    "flow": DataFlowKind.RETURN_TO_CALL.value,
                    "result_targets": list(call.attributes.get("result_targets", [])),
                    "return_types": list(target.attributes.get("return_types", [])),
                },
            )
            result.edges.append(edge)
            result.facts.append(
                self._flow_fact(
                    return_operation,
                    call,
                    DataFlowKind.RETURN_TO_CALL,
                    edge.attributes,
                )
            )
            result.return_edges += 1

    def _link_arguments(
        self,
        call: SemanticOperation,
        target: SemanticOperation,
        parameters: list[SemanticOperation],
        result: SemanticLinkResult,
    ) -> None:
        arguments = call.attributes.get("arguments", [])
        argument_names = call.attributes.get("argument_names", [])
        if not isinstance(arguments, list) or not isinstance(argument_names, list):
            return
        parameters = [
            parameter
            for parameter in parameters
            if not bool(parameter.attributes.get("receiver", False))
        ]
        fixed = [
            parameter
            for parameter in parameters
            if parameter.attributes.get("parameter_kind")
            not in {"keyword_only", "vararg", "variadic", "kwarg"}
        ]
        variadic = next(
            (
                parameter
                for parameter in parameters
                if parameter.attributes.get("parameter_kind") in {"vararg", "variadic"}
            ),
            None,
        )
        keyword_sink = next(
            (
                parameter
                for parameter in parameters
                if parameter.attributes.get("parameter_kind") == "kwarg"
            ),
            None,
        )
        positional_index = 0
        for argument_index, expression in enumerate(arguments):
            argument_name = (
                str(argument_names[argument_index]) if argument_index < len(argument_names) else ""
            )
            parameter: SemanticOperation | None
            if argument_name:
                parameter = next(
                    (candidate for candidate in parameters if candidate.target == argument_name),
                    keyword_sink,
                )
            elif positional_index < len(fixed):
                parameter = fixed[positional_index]
                positional_index += 1
            else:
                parameter = variadic
            if parameter is None:
                continue
            attributes = {
                "flow": DataFlowKind.ARGUMENT_TO_PARAMETER.value,
                "argument_index": argument_index,
                "argument_name": argument_name,
                "expression": str(expression),
                "parameter": parameter.target,
                "target_function": target.id,
            }
            edge = OperationEdge(
                source_id=call.id,
                target_id=parameter.id,
                kind=OperationEdgeKind.DATA_DEPENDENCY,
                attributes=attributes,
            )
            result.edges.append(edge)
            result.facts.append(
                self._flow_fact(
                    call,
                    parameter,
                    DataFlowKind.ARGUMENT_TO_PARAMETER,
                    attributes,
                )
            )
            result.argument_edges += 1

    @staticmethod
    def _call_fact(
        call: SemanticOperation,
        target: SemanticOperation,
        resolution: str,
    ) -> SemanticFact:
        qualified = str(target.attributes.get("qualified_name", target.target))
        return SemanticFact(
            subject=call.scope_id,
            predicate=FactKind.CALLS,
            object=qualified,
            evidence=(call.location, target.location),
            attributes={
                "link_kind": "interprocedural_call",
                "call_operation": call.id,
                "target_operation": target.id,
                "call_kind": call.kind.value,
                "resolution": resolution,
            },
        )

    @staticmethod
    def _flow_fact(
        source: SemanticOperation,
        target: SemanticOperation,
        flow: DataFlowKind,
        attributes: dict[str, object] | object,
    ) -> SemanticFact:
        details = dict(attributes) if isinstance(attributes, dict) else {}
        return SemanticFact(
            subject=source.id,
            predicate=FactKind.FLOWS_TO,
            object=target.id,
            evidence=(source.location, target.location),
            attributes={"link_kind": flow.value, **details},
        )

    @staticmethod
    def _link_synchronization(
        operations: Iterable[SemanticOperation],
        result: SemanticLinkResult,
    ) -> None:
        sends: dict[tuple[str, str, str], list[SemanticOperation]] = defaultdict(list)
        receives: dict[tuple[str, str, str], list[SemanticOperation]] = defaultdict(list)
        for operation in operations:
            channel = _normalize_symbol(str(operation.attributes.get("channel", "")))
            if not channel:
                continue
            key = (operation.language.lower(), operation.scope_id, channel)
            if operation.kind == OperationKind.SEND:
                sends[key].append(operation)
            elif operation.kind == OperationKind.RECEIVE:
                receives[key].append(operation)
        for key in sorted(set(sends).intersection(receives)):
            for send in sorted(sends[key], key=_operation_order):
                for receive in sorted(receives[key], key=_operation_order):
                    result.edges.append(
                        OperationEdge(
                            source_id=send.id,
                            target_id=receive.id,
                            kind=OperationEdgeKind.SYNCHRONIZES,
                            attributes={"channel": key[2], "scope": "intraprocedural"},
                        )
                    )
                    result.facts.append(
                        SemanticFact(
                            subject=send.scope_id,
                            predicate=FactKind.FLOWS_TO,
                            object=key[2],
                            evidence=(send.location, receive.location),
                            attributes={
                                "link_kind": "channel_synchronization",
                                "send_operation": send.id,
                                "receive_operation": receive.id,
                            },
                        )
                    )
                    result.synchronization_edges += 1


class InterproceduralGraph:
    """Read-only, depth-bounded queries over a linked SemanticIR."""

    def __init__(self, ir: SemanticIR) -> None:
        self._operations = {operation.id: operation for operation in ir.operations}
        self._call_edges = sorted(
            (edge for edge in ir.operation_edges if edge.kind == OperationEdgeKind.CALLS),
            key=_edge_order,
        )
        self._data_edges = sorted(
            (edge for edge in ir.operation_edges if edge.kind == OperationEdgeKind.DATA_DEPENDENCY),
            key=_edge_order,
        )

    def callees(self, call_id: str) -> tuple[SemanticOperation, ...]:
        return tuple(
            self._operations[edge.target_id]
            for edge in self._call_edges
            if edge.source_id == call_id and edge.target_id in self._operations
        )

    def callers(self, function_id: str) -> tuple[SemanticOperation, ...]:
        return tuple(
            self._operations[edge.source_id]
            for edge in self._call_edges
            if edge.target_id == function_id and edge.source_id in self._operations
        )

    def argument_bindings(self, call_id: str) -> tuple[OperationEdge, ...]:
        return tuple(
            edge
            for edge in self._data_edges
            if edge.source_id == call_id
            and edge.attributes.get("flow") == DataFlowKind.ARGUMENT_TO_PARAMETER.value
        )

    def return_sources(self, call_id: str) -> tuple[SemanticOperation, ...]:
        return tuple(
            self._operations[edge.source_id]
            for edge in self._data_edges
            if edge.target_id == call_id
            and edge.attributes.get("flow") == DataFlowKind.RETURN_TO_CALL.value
            and edge.source_id in self._operations
        )

    def call_path(
        self,
        source_function_id: str,
        target_function_id: str,
        *,
        max_depth: int = 4,
    ) -> tuple[SemanticOperation, ...]:
        """Return the shortest deterministic function path within ``max_depth``."""
        if not 0 <= max_depth <= _MAX_CALL_DEPTH:
            raise ValueError(f"max_depth must be between 0 and {_MAX_CALL_DEPTH}")
        source = self._operations.get(source_function_id)
        target = self._operations.get(target_function_id)
        if source is None or target is None:
            return ()
        if source_function_id == target_function_id:
            return (source,)

        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self._call_edges:
            call = self._operations.get(edge.source_id)
            if call is not None:
                adjacency[call.scope_id].add(edge.target_id)
        queue: deque[tuple[str, ...]] = deque([(source_function_id,)])
        visited = {source_function_id}
        while queue:
            path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue
            for next_id in sorted(adjacency.get(path[-1], set())):
                next_path = (*path, next_id)
                if next_id == target_function_id:
                    return tuple(self._operations[item] for item in next_path)
                if next_id not in visited:
                    visited.add(next_id)
                    queue.append(next_path)
        return ()


def _normalize_symbol(value: str) -> str:
    return "".join(value.strip().split()).replace("::", ".")


def _normalize_type(value: str) -> str:
    normalized = "".join(value.strip().split()).strip("'\"")
    while normalized.startswith(("*", "&")):
        normalized = normalized[1:]
    if normalized.startswith("Optional[") and normalized.endswith("]"):
        normalized = normalized[9:-1]
    if any(token in normalized for token in ("|", "Union[")):
        return ""
    return normalized


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, str]:
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        operation.id,
    )


def _edge_order(edge: OperationEdge) -> tuple[str, str, str, str]:
    return (
        edge.source_id,
        edge.target_id,
        edge.kind.value,
        json.dumps(dict(edge.attributes), sort_keys=True, ensure_ascii=False, default=str),
    )


def _edge_key(edge: OperationEdge) -> tuple[str, str, OperationEdgeKind, str]:
    return (
        edge.source_id,
        edge.target_id,
        edge.kind,
        json.dumps(dict(edge.attributes), sort_keys=True, ensure_ascii=False, default=str),
    )
