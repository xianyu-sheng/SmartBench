"""Conservative language-neutral linking of calls and synchronization operations."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from smartbench.ir import (
    FactKind,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticFact,
    SemanticIR,
    SemanticOperation,
)

_CALL_KINDS = frozenset({OperationKind.CALL, OperationKind.SPAWN, OperationKind.DEFER})


@dataclass
class SemanticLinkResult:
    """Edges and facts inferred without guessing ambiguous symbols."""

    edges: list[OperationEdge] = field(default_factory=list)
    facts: list[SemanticFact] = field(default_factory=list)
    resolved_calls: int = 0
    unresolved_calls: int = 0
    ambiguous_calls: int = 0
    synchronization_edges: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "resolved_calls": self.resolved_calls,
            "unresolved_calls": self.unresolved_calls,
            "ambiguous_calls": self.ambiguous_calls,
            "call_edges": sum(edge.kind == OperationEdgeKind.CALLS for edge in self.edges),
            "synchronization_edges": self.synchronization_edges,
        }


class SemanticLinker:
    """Resolve normalized operations across files using conservative symbol rules."""

    def link(self, ir: SemanticIR) -> SemanticLinkResult:
        result = SemanticLinkResult()
        functions = sorted(
            (operation for operation in ir.operations if operation.kind == OperationKind.FUNCTION),
            key=_operation_order,
        )
        by_id = {operation.id: operation for operation in ir.operations}
        exact, by_namespace, by_simple = self._function_indices(functions)

        for call in sorted(
            (operation for operation in ir.operations if operation.kind in _CALL_KINDS),
            key=_operation_order,
        ):
            caller = by_id.get(call.scope_id)
            candidates, resolution = self._resolve_call(
                call,
                caller,
                exact,
                by_namespace,
                by_simple,
            )
            if len(candidates) == 1:
                target = candidates[0]
                edge = OperationEdge(
                    source_id=call.id,
                    target_id=target.id,
                    kind=OperationEdgeKind.CALLS,
                    attributes={
                        "resolution": resolution,
                        "call_kind": call.kind.value,
                    },
                )
                result.edges.append(edge)
                result.facts.append(self._call_fact(call, target, resolution))
                result.resolved_calls += 1
            elif candidates:
                result.ambiguous_calls += 1
            else:
                result.unresolved_calls += 1

        self._link_synchronization(ir.operations, result)
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
    ) -> tuple[
        dict[tuple[str, str], list[SemanticOperation]],
        dict[tuple[str, str], list[SemanticOperation]],
        dict[tuple[str, str], list[SemanticOperation]],
    ]:
        exact: dict[tuple[str, str], list[SemanticOperation]] = defaultdict(list)
        by_namespace: dict[tuple[str, str], list[SemanticOperation]] = defaultdict(list)
        by_simple: dict[tuple[str, str], list[SemanticOperation]] = defaultdict(list)
        for function in functions:
            language = function.language.lower()
            qualified = str(function.attributes.get("qualified_name", function.target))
            namespace = str(function.attributes.get("namespace", ""))
            simple = str(function.attributes.get("symbol_name", function.target)).split(".")[-1]
            exact[(language, _normalize_symbol(qualified))].append(function)
            if namespace:
                by_namespace[(language, f"{_normalize_symbol(namespace)}::{simple}")].append(function)
            by_simple[(language, simple)].append(function)
        return exact, by_namespace, by_simple

    def _resolve_call(
        self,
        call: SemanticOperation,
        caller: SemanticOperation | None,
        exact: dict[tuple[str, str], list[SemanticOperation]],
        by_namespace: dict[tuple[str, str], list[SemanticOperation]],
        by_simple: dict[tuple[str, str], list[SemanticOperation]],
    ) -> tuple[list[SemanticOperation], str]:
        language = call.language.lower()
        target = _normalize_symbol(call.target)
        if not target:
            return [], "empty"

        exact_candidates = exact.get((language, target), [])
        if exact_candidates:
            return exact_candidates, "qualified"

        namespace = str(caller.attributes.get("namespace", "")) if caller else ""
        simple = target.split(".")[-1]
        if namespace:
            namespace_key = (language, f"{_normalize_symbol(namespace)}::{simple}")
            namespace_candidates = by_namespace.get(namespace_key, [])
            if len(namespace_candidates) == 1 and "." not in target:
                return namespace_candidates, "namespace"

        contextual = self._contextual_method_target(target, caller)
        if contextual:
            contextual_candidates = exact.get((language, contextual), [])
            if contextual_candidates:
                return contextual_candidates, "lexical_receiver"

        # Dotted targets require an exact or lexical proof. A unique final
        # component is insufficient because receiver types are unresolved.
        if "." in target:
            return [], "unresolved_receiver"
        return by_simple.get((language, simple), []), "unique_simple"

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
                    result.edges.append(OperationEdge(
                        source_id=send.id,
                        target_id=receive.id,
                        kind=OperationEdgeKind.SYNCHRONIZES,
                        attributes={"channel": key[2], "scope": "intraprocedural"},
                    ))
                    result.facts.append(SemanticFact(
                        subject=send.scope_id,
                        predicate=FactKind.FLOWS_TO,
                        object=key[2],
                        evidence=(send.location, receive.location),
                        attributes={
                            "link_kind": "channel_synchronization",
                            "send_operation": send.id,
                            "receive_operation": receive.id,
                        },
                    ))
                    result.synchronization_edges += 1


def _normalize_symbol(value: str) -> str:
    return "".join(value.strip().split()).replace("::", ".")


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, str]:
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        operation.id,
    )


def _edge_key(edge: OperationEdge) -> tuple[str, str, OperationEdgeKind, str]:
    return (
        edge.source_id,
        edge.target_id,
        edge.kind,
        json.dumps(dict(edge.attributes), sort_keys=True, ensure_ascii=False, default=str),
    )
