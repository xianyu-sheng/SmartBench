"""Language-neutral resource lifecycle protocols over normalized operations.

This module deliberately separates two questions:

1. Which call returns a resource that participates in a cleanup protocol?
2. Does cleanup registration dominate the resource's subsequent use?

The first answer may come from a project reader or a source-backed reference.
The second answer is always computed deterministically from SemanticIR.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from smartbench.analysis.control_flow import ControlFlowGraph
from smartbench.ir import (
    FactKind,
    OperationEdgeKind,
    OperationKind,
    SemanticFact,
    SemanticIR,
    SemanticOperation,
    TypeEvidenceIndex,
    TypeEvidenceRole,
    type_names_compatible,
)


class ProtocolOrigin(str, Enum):
    """Provenance of a project-specific semantic mapping."""

    REFERENCE_USAGE = "reference_usage"
    PROJECT_READER = "project_reader"


class AcquireMatchMode(str, Enum):
    """How a validated acquire exemplar may match calls under analysis."""

    EXACT = "exact"
    METHOD_SHAPE = "method_shape"
    TYPED_METHOD = "typed_method"


@dataclass(frozen=True)
class ResourceProtocol:
    """A portable, project-scoped resource protocol hypothesis."""

    acquire_symbol: str
    resource_result_index: int
    cleanup_methods: tuple[str, ...]
    acquire_match_mode: AcquireMatchMode = AcquireMatchMode.EXACT
    resource_member_path: str = ""
    receiver_type: str = ""
    canonical_acquire: str = ""
    type_evidence_ids: tuple[str, ...] = ()
    origin: ProtocolOrigin = ProtocolOrigin.PROJECT_READER
    confidence: float = 0.5
    evidence_fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.acquire_symbol.strip():
            raise ValueError("acquire_symbol must not be empty")
        if self.resource_result_index < 0:
            raise ValueError("resource_result_index must be non-negative")
        if not self.cleanup_methods or any(not item.strip() for item in self.cleanup_methods):
            raise ValueError("cleanup_methods must contain non-empty method names")
        if not isinstance(self.acquire_match_mode, AcquireMatchMode):
            raise ValueError("acquire_match_mode must be an AcquireMatchMode")
        if self.resource_member_path and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
            self.resource_member_path,
        ):
            raise ValueError("resource_member_path must be a dotted identifier path")
        if self.acquire_match_mode in {
            AcquireMatchMode.METHOD_SHAPE,
            AcquireMatchMode.TYPED_METHOD,
        } and not self.resource_member_path:
            raise ValueError("generalized matching requires a resource member path")
        if self.acquire_match_mode == AcquireMatchMode.TYPED_METHOD:
            if not self.receiver_type or not self.canonical_acquire:
                raise ValueError(
                    "typed_method matching requires receiver type and canonical acquire"
                )
            if not self.type_evidence_ids:
                raise ValueError("typed_method matching requires source type evidence")
        elif self.receiver_type or self.canonical_acquire or self.type_evidence_ids:
            raise ValueError("type evidence fields are reserved for typed_method matching")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("protocol confidence must be between 0 and 1")

    @property
    def protocol_id(self) -> str:
        material = "|".join(
            (
                self.acquire_symbol,
                str(self.resource_result_index),
                self.acquire_match_mode.value,
                self.resource_member_path,
                self.receiver_type,
                self.canonical_acquire,
                *sorted(self.type_evidence_ids),
                *sorted(self.cleanup_methods),
            )
        )
        return "protocol-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ResourceLifecycleFinding:
    """A deterministic path witness for an unprotected resource use."""

    protocol: ResourceProtocol
    resource_binding: str
    acquire: SemanticOperation
    first_unprotected_use: SemanticOperation
    cleanup_candidates: tuple[SemanticOperation, ...] = ()
    matched_type_evidence_ids: tuple[str, ...] = ()

    def to_fact(self) -> SemanticFact:
        evidence = [self.acquire.location, self.first_unprotected_use.location]
        evidence.extend(operation.location for operation in self.cleanup_candidates[:3])
        return SemanticFact(
            subject=self.acquire.scope_id,
            predicate=FactKind.STATE_TRANSITION,
            object="resource use is not dominated by cleanup registration",
            evidence=tuple(evidence),
            confidence=self.protocol.confidence,
            attributes={
                "protocol_id": self.protocol.protocol_id,
                "protocol_origin": self.protocol.origin.value,
                "acquire_symbol": self.protocol.acquire_symbol,
                "matched_acquire_symbol": self.acquire.target,
                "acquire_match_mode": self.protocol.acquire_match_mode.value,
                "resource_binding": self.resource_binding,
                "resource_member_path": self.protocol.resource_member_path,
                "cleanup_methods": list(self.protocol.cleanup_methods),
                "receiver_type": self.protocol.receiver_type,
                "canonical_acquire": self.protocol.canonical_acquire,
                "reference_type_evidence_ids": list(self.protocol.type_evidence_ids),
                "matched_type_evidence_ids": list(self.matched_type_evidence_ids),
                "acquire_operation": self.acquire.id,
                "use_operation": self.first_unprotected_use.id,
                "cleanup_operations": [item.id for item in self.cleanup_candidates],
                "proof": "cfg_dominance_between_acquire_and_use",
                "mapping_fact_ids": list(self.protocol.evidence_fact_ids),
            },
        )


@dataclass
class ResourceLifecycleResult:
    findings: list[ResourceLifecycleFinding] = field(default_factory=list)
    protocols_evaluated: int = 0
    acquisitions_evaluated: int = 0
    abstentions: int = 0
    unknown_reasons: list[str] = field(default_factory=list)


class ResourceProtocolMiner:
    """Learn protocol mappings from source-backed successful cleanup usage.

    This deterministic reference reader supplies reproducible hypotheses with
    the same portable shape as future ProjectReader Agent output.
    """

    def learn(
        self,
        ir: SemanticIR,
        *,
        generalize_method_shapes: bool = False,
    ) -> list[ResourceProtocol]:
        cfg = ControlFlowGraph.from_ir(ir)
        operations = {operation.id: operation for operation in ir.operations}
        type_index = TypeEvidenceIndex(ir.type_evidence)
        protocols: dict[
            tuple[
                str,
                int,
                AcquireMatchMode,
                str,
                str,
                str,
                tuple[str, ...],
            ],
            ResourceProtocol,
        ] = {}
        for call in sorted(ir.operations, key=_operation_order):
            if not _is_primary_result_call(call, operations):
                continue
            result_targets = _result_targets(call)
            for index, binding in enumerate(result_targets):
                if not _usable_binding(binding):
                    continue
                cleanup_operations = [
                    operation
                    for operation in ir.operations
                    if operation.scope_id == call.scope_id
                    and operation.kind == OperationKind.DEFER
                    and cfg.reachable(call.id, operation.id)
                    and _receiver_root(operation) == binding
                ]
                member_paths = {
                    path
                    for operation in cleanup_operations
                    if (path := _receiver_member_path(operation, binding)) is not None
                }
                for member_path in sorted(member_paths):
                    methods = tuple(
                        sorted(
                            {
                                method
                                for operation in cleanup_operations
                                if _receiver_member_path(operation, binding) == member_path
                                and (method := _method_name(operation))
                            }
                        )
                    )
                    if not methods:
                        continue
                    receiver_type = ""
                    canonical_acquire = ""
                    type_evidence_ids: tuple[str, ...] = ()
                    match_mode = AcquireMatchMode.EXACT
                    if generalize_method_shapes and member_path:
                        receiver_type = type_index.unique_type(
                            call.id, TypeEvidenceRole.RECEIVER
                        )
                        canonical_symbols = type_index.canonical_symbols(call.id)
                        type_evidence_ids = type_index.evidence_ids(
                            call.id, TypeEvidenceRole.RECEIVER
                        )
                        if (
                            receiver_type
                            and len(canonical_symbols) == 1
                            and type_evidence_ids
                        ):
                            match_mode = AcquireMatchMode.TYPED_METHOD
                            canonical_acquire = canonical_symbols[0]
                        else:
                            match_mode = AcquireMatchMode.METHOD_SHAPE
                    key = (
                        call.target,
                        index,
                        match_mode,
                        member_path,
                        receiver_type,
                        canonical_acquire,
                        methods,
                    )
                    existing = protocols.get(key)
                    if existing is not None:
                        type_evidence_ids = tuple(
                            sorted(
                                {
                                    *existing.type_evidence_ids,
                                    *type_evidence_ids,
                                }
                            )
                        )
                    protocols[key] = ResourceProtocol(
                        acquire_symbol=call.target,
                        resource_result_index=index,
                        cleanup_methods=methods,
                        acquire_match_mode=match_mode,
                        resource_member_path=member_path,
                        receiver_type=receiver_type,
                        canonical_acquire=canonical_acquire,
                        type_evidence_ids=type_evidence_ids,
                        origin=ProtocolOrigin.REFERENCE_USAGE,
                        confidence={
                            AcquireMatchMode.EXACT: 1.0,
                            AcquireMatchMode.TYPED_METHOD: 0.95,
                            AcquireMatchMode.METHOD_SHAPE: 0.8,
                        }[match_mode],
                    )
        return [protocols[key] for key in sorted(protocols)]


class _OwnershipDisposition(str, Enum):
    CLOSES = "closes"
    LEAKS = "leaks"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _OwnershipProof:
    disposition: _OwnershipDisposition
    reason: str


class _OwnershipFunctionIndex:
    """Conservative summaries for unique local free functions."""

    def __init__(self, ir: SemanticIR, cfg: ControlFlowGraph) -> None:
        self.cfg = cfg
        self.operations_by_scope: dict[str, list[SemanticOperation]] = {}
        for operation in ir.operations:
            self.operations_by_scope.setdefault(operation.scope_id, []).append(operation)
        self.functions = tuple(
            operation
            for operation in ir.operations
            if operation.kind == OperationKind.FUNCTION
            and not str(operation.attributes.get("receiver_type", "")).strip()
            and "." not in str(operation.attributes.get("lexical_name", ""))
        )
        self.cache: dict[
            tuple[str, str, str, tuple[str, ...]],
            _OwnershipProof,
        ] = {}

    def prove_transfer(
        self,
        call: SemanticOperation,
        argument_index: int,
        member_path: str,
        cleanup_methods: tuple[str, ...],
        stack: tuple[str, ...] = (),
    ) -> _OwnershipProof:
        function, resolution_reason = self._resolve(call)
        if function is None:
            return _OwnershipProof(_OwnershipDisposition.UNKNOWN, resolution_reason)
        parameter = self._parameter(function, call, argument_index)
        if parameter is None:
            return _OwnershipProof(
                _OwnershipDisposition.UNKNOWN,
                f"argument {argument_index} cannot be mapped to a positional parameter "
                f"of {function.target}",
            )
        key = (function.id, parameter.id, member_path, cleanup_methods)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if function.id in stack:
            return _OwnershipProof(
                _OwnershipDisposition.UNKNOWN,
                f"recursive ownership summary for {function.target}",
            )
        proof = self._summarize(
            function,
            parameter,
            member_path,
            cleanup_methods,
            (*stack, function.id),
        )
        self.cache[key] = proof
        return proof

    def _resolve(
        self,
        call: SemanticOperation,
    ) -> tuple[SemanticOperation | None, str]:
        if str(call.attributes.get("receiver", "")).strip():
            return None, f"{call.target} is a method call, not a local free function"
        candidates = [
            function
            for function in self.functions
            if function.language.lower() == call.language.lower()
            and (
                function.target == call.target
                or str(function.attributes.get("qualified_name", "")) == call.target
            )
        ]
        if len(candidates) == 1:
            return candidates[0], ""
        if not candidates:
            return None, f"no local free function resolves {call.target}"
        return None, f"multiple local free functions resolve {call.target}"

    def _parameter(
        self,
        function: SemanticOperation,
        call: SemanticOperation,
        argument_index: int,
    ) -> SemanticOperation | None:
        parameters = [
            operation
            for operation in self._scope_operations(function)
            if operation.kind == OperationKind.PARAMETER
            and operation.attributes.get("parameter_kind") == "positional"
            and isinstance(operation.attributes.get("position"), int)
        ]
        argument_names = call.attributes.get("argument_names", ())
        if (
            isinstance(argument_names, (list, tuple))
            and argument_index < len(argument_names)
            and str(argument_names[argument_index]).strip()
        ):
            name = str(argument_names[argument_index]).strip()
            matches = [parameter for parameter in parameters if parameter.target == name]
        else:
            matches = [
                parameter
                for parameter in parameters
                if parameter.attributes["position"] == argument_index
            ]
        return matches[0] if len(matches) == 1 else None

    def _scope_operations(self, function: SemanticOperation) -> list[SemanticOperation]:
        return self.operations_by_scope.get(function.id, [])

    def _summarize(
        self,
        function: SemanticOperation,
        parameter: SemanticOperation,
        member_path: str,
        cleanup_methods: tuple[str, ...],
        stack: tuple[str, ...],
    ) -> _OwnershipProof:
        body = [
            operation
            for operation in self._scope_operations(function)
            if operation.kind not in {OperationKind.FUNCTION, OperationKind.PARAMETER}
        ]
        if not body:
            return _OwnershipProof(
                _OwnershipDisposition.LEAKS,
                f"{function.target} has no operation that closes its resource parameter",
            )
        aliases, alias_assignment_ids, alias_definitions = _parameter_aliases(
            body, parameter.target
        )
        cleanup_ids: set[str] = set()
        for operation in body:
            active_aliases = _active_aliases(
                parameter.target,
                aliases,
                alias_definitions,
                operation,
            )
            if (
                operation.kind in {OperationKind.CALL, OperationKind.DEFER}
                and _method_name(operation) in cleanup_methods
                and any(
                    _receiver_member_path(operation, alias) == member_path
                    for alias in active_aliases
                )
            ):
                cleanup_ids.add(operation.id)
        protections = [
            operation for operation in body if operation.id in cleanup_ids
        ]
        unknown_reasons: list[str] = []
        ownership_call_hosts: set[str] = set()
        for operation in body:
            if operation.kind != OperationKind.CALL or operation.id in cleanup_ids:
                continue
            active_aliases = _active_aliases(
                parameter.target,
                aliases,
                alias_definitions,
                operation,
            )
            argument_index = _resource_argument_index(
                operation, active_aliases, member_path
            )
            if argument_index is None:
                continue
            ownership_call_hosts.add(_host_id(operation))
            argument = str(operation.attributes["arguments"][argument_index]).strip()
            effective_path = (
                ""
                if member_path
                and any(
                    argument == f"{alias}.{member_path}" for alias in active_aliases
                )
                else member_path
            )
            proof = self.prove_transfer(
                operation,
                argument_index,
                effective_path,
                cleanup_methods,
                stack,
            )
            if proof.disposition == _OwnershipDisposition.CLOSES:
                protections.append(operation)
            elif proof.disposition == _OwnershipDisposition.UNKNOWN:
                unknown_reasons.append(
                    f"{function.target} calls {operation.target}: {proof.reason}"
                )

        for operation in body:
            if (
                operation.id in cleanup_ids
                or operation.id in alias_assignment_ids
                or operation.id in {item.id for item in protections}
                or operation.id in ownership_call_hosts
                or _host_id(operation) in cleanup_ids | {item.id for item in protections}
            ):
                continue
            active_aliases = _active_aliases(
                parameter.target,
                aliases,
                alias_definitions,
                operation,
            )
            if any(
                _uses_resource(operation, alias, member_path)
                for alias in active_aliases
            ) and operation.kind not in {
                OperationKind.CALL,
                OperationKind.DEFER,
                OperationKind.RETURN,
            }:
                unknown_reasons.append(
                    f"{function.target} has unsupported resource operation "
                    f"{operation.kind.value} at {operation.id}"
                )
            if operation.kind == OperationKind.RETURN and _returns_alias(
                operation,
                active_aliases,
                member_path,
            ):
                unknown_reasons.append(
                    f"{function.target} returns an aliased resource without a proven close"
                )

        coverage, reason = self._cleanup_coverage(body, protections)
        if coverage:
            return _OwnershipProof(
                _OwnershipDisposition.CLOSES,
                f"{function.target} closes the resource on every represented exit",
            )
        if coverage is None:
            return _OwnershipProof(
                _OwnershipDisposition.UNKNOWN,
                f"{function.target}: {reason}",
            )
        if unknown_reasons:
            return _OwnershipProof(
                _OwnershipDisposition.UNKNOWN,
                "; ".join(dict.fromkeys(unknown_reasons)),
            )
        return _OwnershipProof(
            _OwnershipDisposition.LEAKS,
            f"{function.target} has an exit without resource cleanup",
        )

    def _cleanup_coverage(
        self,
        body: list[SemanticOperation],
        protections: list[SemanticOperation],
    ) -> tuple[bool | None, str]:
        body_ids = {operation.id for operation in body}
        entries = [
            operation
            for operation in body
            if not any(
                arc.source_id in body_ids
                for arc in self.cfg.predecessors(operation.id)
            )
        ]
        exits = [
            operation
            for operation in body
            if not self.cfg.successors(operation.id)
            or operation.kind in {OperationKind.RETURN, OperationKind.RAISE}
        ]
        if not entries or not exits:
            return None, "function entry or exit coverage is unavailable"
        for operation in body:
            successor_kinds = {
                arc.kind for arc in self.cfg.successors(operation.id)
            }
            if operation.kind == OperationKind.BRANCH and not {
                OperationEdgeKind.TRUE_BRANCH,
                OperationEdgeKind.FALSE_BRANCH,
            }.issubset(successor_kinds):
                return None, "function CFG has an incomplete branch outcome"
            if operation.kind == OperationKind.LOOP and not {
                OperationEdgeKind.BODY,
                OperationEdgeKind.LOOP_EXIT,
            }.issubset(successor_kinds):
                return None, "function CFG has an incomplete loop exit"
        for entry in entries:
            reachable_exits = [
                exit_operation
                for exit_operation in exits
                if entry.id == exit_operation.id
                or self.cfg.reachable(entry.id, exit_operation.id)
            ]
            if not reachable_exits:
                return None, "function CFG does not connect entry to an exit"
            for exit_operation in reachable_exits:
                covered = any(
                    self.cfg.dominates_between(
                        entry.id,
                        protection.id,
                        exit_operation.id,
                    )
                    for protection in protections
                )
                if not covered:
                    return False, "at least one represented exit bypasses cleanup"
        return True, ""


def _parameter_aliases(
    body: list[SemanticOperation],
    parameter: str,
) -> tuple[set[str], set[str], dict[str, tuple[str, int, int, str]]]:
    aliases = {parameter}
    alias_assignment_ids: set[str] = set()
    alias_definitions: dict[str, tuple[str, int, int, str]] = {}
    if any(
        operation.kind in {OperationKind.BRANCH, OperationKind.LOOP}
        for operation in body
    ):
        return aliases, alias_assignment_ids, alias_definitions
    for operation in sorted(body, key=_operation_order):
        if operation.kind != OperationKind.ASSIGN:
            continue
        raw_bindings = operation.attributes.get("bindings", ())
        targets = []
        if isinstance(raw_bindings, (list, tuple)):
            for binding in raw_bindings:
                if isinstance(binding, dict) and binding.get("target"):
                    targets.append(str(binding["target"]))
        if not targets:
            targets = [operation.target] if operation.target else []
        value = operation.value
        if ":=" in value:
            rhs = value.split(":=", 1)[1].strip()
        elif re.search(r"(?<![=!<>])=(?!=)", value):
            rhs = re.split(r"(?<![=!<>])=(?!=)", value, maxsplit=1)[1].strip()
        else:
            rhs = value.strip()
        for target in targets:
            if rhs in aliases:
                aliases.add(target)
                alias_assignment_ids.add(operation.id)
                alias_definitions[target] = _operation_order(operation)
            else:
                aliases.discard(target)
                alias_definitions.pop(target, None)
    return aliases, alias_assignment_ids, alias_definitions


def _active_aliases(
    parameter: str,
    aliases: set[str],
    definitions: dict[str, tuple[str, int, int, str]],
    operation: SemanticOperation,
) -> set[str]:
    operation_order = _operation_order(operation)
    active: set[str] = set()
    for alias in aliases:
        if alias == parameter:
            active.add(alias)
        elif (definition := definitions.get(alias)) is not None and definition <= operation_order:
            active.add(alias)
    return active


def _returns_alias(
    operation: SemanticOperation,
    aliases: set[str],
    member_path: str,
) -> bool:
    raw_values = operation.attributes.get("values", ())
    values = (
        [str(value).strip() for value in raw_values]
        if isinstance(raw_values, (list, tuple))
        else [item.strip() for item in operation.value.split(",")]
    )
    resources = set(aliases)
    if member_path:
        resources.update(f"{alias}.{member_path}" for alias in aliases)
    return any(value in resources for value in values)


def _resource_argument_index(
    operation: SemanticOperation,
    aliases: set[str] | tuple[str, ...],
    member_path: str,
) -> int | None:
    arguments = operation.attributes.get("arguments", ())
    if not isinstance(arguments, (list, tuple)):
        return None
    resources = set(aliases)
    if member_path:
        resources.update(f"{alias}.{member_path}" for alias in aliases)
    for index, argument in enumerate(arguments):
        if str(argument).strip() in resources:
            return index
    return None


class ResourceLifecycleAnalyzer:
    """Verify cleanup-before-use invariants without language-specific syntax."""

    def analyze(
        self,
        ir: SemanticIR,
        protocols: Iterable[ResourceProtocol],
    ) -> ResourceLifecycleResult:
        protocol_list = list(protocols)
        result = ResourceLifecycleResult(protocols_evaluated=len(protocol_list))
        cfg = ControlFlowGraph.from_ir(ir)
        ownership_index = _OwnershipFunctionIndex(ir, cfg)
        type_index = TypeEvidenceIndex(ir.type_evidence)
        operations = {operation.id: operation for operation in ir.operations}
        ordered = sorted(ir.operations, key=_operation_order)

        for protocol in protocol_list:
            acquisitions = [
                operation
                for operation in ordered
                if operation.kind == OperationKind.CALL
                and _matches_acquire(operation, protocol, type_index)
                and _is_primary_result_call(operation, operations)
            ]
            if not acquisitions:
                result.abstentions += 1
                result.unknown_reasons.append(
                    _missing_acquire_reason(protocol)
                )
                continue
            for acquire in acquisitions:
                result.acquisitions_evaluated += 1
                targets = _result_targets(acquire)
                if protocol.resource_result_index >= len(targets):
                    result.abstentions += 1
                    result.unknown_reasons.append(
                        f"{protocol.protocol_id}: result index is unavailable at {acquire.id}"
                    )
                    continue
                binding = targets[protocol.resource_result_index]
                if not _usable_binding(binding):
                    result.abstentions += 1
                    result.unknown_reasons.append(
                        f"{protocol.protocol_id}: result binding is not usable at {acquire.id}"
                    )
                    continue

                scope_body = [
                    operation
                    for operation in ordered
                    if operation.scope_id == acquire.scope_id
                    and operation.id not in {acquire.id, _host_id(acquire)}
                    and cfg.reachable(acquire.id, operation.id)
                ]
                aliases, alias_assignment_ids, alias_definitions = _parameter_aliases(
                    scope_body, binding
                )
                cleanup_operations: list[SemanticOperation] = []
                for operation in ordered:
                    if (
                        operation.scope_id != acquire.scope_id
                        or operation.kind != OperationKind.DEFER
                        or _method_name(operation) not in protocol.cleanup_methods
                        or not cfg.reachable(acquire.id, operation.id)
                    ):
                        continue
                    active_aliases = _active_aliases(
                        binding,
                        aliases,
                        alias_definitions,
                        operation,
                    )
                    if any(
                        _receiver_member_path(operation, alias)
                        == protocol.resource_member_path
                        for alias in active_aliases
                    ):
                        cleanup_operations.append(operation)
                cleanups = tuple(cleanup_operations)
                ownership_transfers = tuple(
                    operation
                    for operation in ordered
                    if operation.scope_id == acquire.scope_id
                    and _transfers_ownership(
                        operation,
                        _active_aliases(
                            binding,
                            aliases,
                            alias_definitions,
                            operation,
                        ),
                    )
                    and cfg.reachable(acquire.id, operation.id)
                )
                if ownership_transfers:
                    result.abstentions += 1
                    result.unknown_reasons.append(
                        f"{protocol.protocol_id}: ownership of {binding} is transferred "
                        "to the caller"
                    )
                    continue
                transfer_calls: list[SemanticOperation] = []
                unknown_transfers: dict[str, _OwnershipProof] = {}
                for operation in ordered:
                    if (
                        operation.scope_id != acquire.scope_id
                        or operation.kind != OperationKind.CALL
                        or not cfg.reachable(acquire.id, operation.id)
                    ):
                        continue
                    active_aliases = _active_aliases(
                        binding,
                        aliases,
                        alias_definitions,
                        operation,
                    )
                    argument_index = _resource_argument_index(
                        operation,
                        active_aliases,
                        protocol.resource_member_path,
                    )
                    if argument_index is None:
                        continue
                    argument = str(
                        operation.attributes["arguments"][argument_index]
                    ).strip()
                    effective_path = (
                        ""
                        if protocol.resource_member_path
                        and any(
                            argument == f"{alias}.{protocol.resource_member_path}"
                            for alias in active_aliases
                        )
                        else protocol.resource_member_path
                    )
                    proof = ownership_index.prove_transfer(
                        operation,
                        argument_index,
                        effective_path,
                        protocol.cleanup_methods,
                    )
                    if proof.disposition == _OwnershipDisposition.CLOSES:
                        transfer_calls.append(operation)
                    elif proof.disposition == _OwnershipDisposition.UNKNOWN:
                        unknown_transfers[operation.id] = proof
                transfer_ids = {operation.id for operation in transfer_calls}
                uses: list[SemanticOperation] = []
                for operation in ordered:
                    if (
                        operation.scope_id != acquire.scope_id
                        or operation.id in {acquire.id, _host_id(acquire)}
                        or operation.id in transfer_ids
                        or operation.id in alias_assignment_ids
                        or _is_embedded_call(operation)
                        or operation in cleanups
                        or not cfg.reachable(acquire.id, operation.id)
                    ):
                        continue
                    active_aliases = _active_aliases(
                        binding,
                        aliases,
                        alias_definitions,
                        operation,
                    )
                    if any(
                        _uses_resource(
                            operation,
                            alias,
                            protocol.resource_member_path,
                        )
                        for alias in active_aliases
                    ):
                        uses.append(operation)
                if not uses:
                    if transfer_calls:
                        continue
                    result.abstentions += 1
                    result.unknown_reasons.append(
                        f"{protocol.protocol_id}: no reachable resource use for {binding}"
                    )
                    continue

                first_unprotected = next(
                    (
                        use
                        for use in uses
                        if not any(
                            cfg.reachable(acquire.id, protection.id)
                            and cfg.reachable(protection.id, use.id)
                            and cfg.dominates_between(
                                acquire.id,
                                protection.id,
                                use.id,
                            )
                            for protection in (*cleanups, *transfer_calls)
                        )
                    ),
                    None,
                )
                if (
                    first_unprotected is not None
                    and first_unprotected.id in unknown_transfers
                ):
                    proof = unknown_transfers[first_unprotected.id]
                    result.abstentions += 1
                    result.unknown_reasons.append(
                        f"{protocol.protocol_id}: ownership of {binding} passed to "
                        f"{first_unprotected.target} is undecidable: {proof.reason}"
                    )
                    continue
                if first_unprotected is not None:
                    result.findings.append(
                        ResourceLifecycleFinding(
                            protocol=protocol,
                            resource_binding=binding,
                            acquire=acquire,
                            first_unprotected_use=first_unprotected,
                            cleanup_candidates=cleanups,
                            matched_type_evidence_ids=type_index.evidence_ids(
                                acquire.id, TypeEvidenceRole.RECEIVER
                            ),
                        )
                    )
        return result


def _result_targets(operation: SemanticOperation) -> tuple[str, ...]:
    raw = operation.attributes.get("result_targets", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw)


def _host_id(operation: SemanticOperation) -> str:
    return str(operation.attributes.get("host_operation", ""))


def _is_primary_result_call(
    operation: SemanticOperation,
    operations: dict[str, SemanticOperation],
) -> bool:
    if operation.kind != OperationKind.CALL or not _result_targets(operation):
        return False
    host = operations.get(_host_id(operation))
    if host is None or host.kind != OperationKind.ASSIGN:
        return False
    calls = host.attributes.get("calls", ())
    return isinstance(calls, (list, tuple)) and bool(calls) and calls[0] == operation.target


def _is_embedded_call(operation: SemanticOperation) -> bool:
    return operation.kind == OperationKind.CALL and bool(_host_id(operation))


def _usable_binding(binding: str) -> bool:
    return bool(binding) and binding != "_"


def _receiver_root(operation: SemanticOperation) -> str:
    receiver = _normalized_receiver(operation)
    return receiver.split(".", 1)[0].split("(", 1)[0].strip()


def _normalized_receiver(operation: SemanticOperation) -> str:
    receiver = str(operation.attributes.get("receiver", "")).strip()
    if not receiver and "." in operation.target:
        receiver = operation.target.rsplit(".", 1)[0]
    while receiver.startswith(("&", "*", "(")):
        receiver = receiver[1:].lstrip()
    return receiver


def _receiver_member_path(
    operation: SemanticOperation,
    binding: str,
) -> str | None:
    receiver = _normalized_receiver(operation)
    if receiver == binding:
        return ""
    prefix = f"{binding}."
    if receiver.startswith(prefix):
        member_path = receiver[len(prefix) :]
        if re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
            member_path,
        ):
            return member_path
    return None


def _method_name(operation: SemanticOperation) -> str:
    return operation.target.rsplit(".", 1)[-1].strip() if operation.target else ""


def _uses_resource(
    operation: SemanticOperation,
    binding: str,
    member_path: str,
) -> bool:
    resource = f"{binding}.{member_path}" if member_path else binding
    receiver = _normalized_receiver(operation)
    if receiver == resource or receiver.startswith(f"{resource}."):
        return True
    if any(
        operand == resource or operand.startswith(f"{resource}.")
        for operand in operation.operands
    ):
        return True
    if member_path and re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(resource)}(?![A-Za-z0-9_])",
        operation.value,
    ):
        return True
    arguments = operation.attributes.get("arguments", ())
    if isinstance(arguments, (list, tuple)):
        for argument in arguments:
            text = str(argument)
            if text == resource or text.startswith(f"{resource}."):
                return True
    if operation.kind == OperationKind.RETURN:
        returned = [item.strip() for item in operation.value.split(",")]
        if binding in returned:
            # Returning the resource itself transfers ownership to the caller.
            return False
        if member_path:
            return resource in operation.value
        return binding in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", operation.value)
    return False


def _matches_acquire(
    operation: SemanticOperation,
    protocol: ResourceProtocol,
    type_index: TypeEvidenceIndex,
) -> bool:
    if protocol.acquire_match_mode == AcquireMatchMode.EXACT:
        return operation.target == protocol.acquire_symbol
    if "." not in operation.target or "." not in protocol.acquire_symbol:
        return False
    if _method_name(operation) != protocol.acquire_symbol.rsplit(".", 1)[-1]:
        return False
    if protocol.acquire_match_mode == AcquireMatchMode.METHOD_SHAPE:
        return not type_index.for_operation(operation.id, TypeEvidenceRole.RECEIVER)
    receiver_type = type_index.unique_type(operation.id, TypeEvidenceRole.RECEIVER)
    canonical_symbols = type_index.canonical_symbols(operation.id)
    return (
        type_names_compatible(protocol.receiver_type, receiver_type)
        and len(canonical_symbols) == 1
        and canonical_symbols[0] == protocol.canonical_acquire
    )


def _missing_acquire_reason(protocol: ResourceProtocol) -> str:
    if protocol.acquire_match_mode == AcquireMatchMode.TYPED_METHOD:
        return (
            f"{protocol.protocol_id}: no call has compatible receiver type "
            f"{protocol.receiver_type} and canonical symbol {protocol.canonical_acquire}"
        )
    if protocol.acquire_match_mode == AcquireMatchMode.METHOD_SHAPE:
        return (
            f"{protocol.protocol_id}: untyped method shape is absent or cannot "
            "bypass available receiver type evidence"
        )
    return f"{protocol.protocol_id}: acquire symbol not present"


def _transfers_ownership(
    operation: SemanticOperation,
    bindings: Iterable[str],
) -> bool:
    if operation.kind != OperationKind.RETURN:
        return False
    returned = [item.strip() for item in operation.value.split(",")]
    return any(binding in returned for binding in bindings)


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, str]:
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        operation.id,
    )
