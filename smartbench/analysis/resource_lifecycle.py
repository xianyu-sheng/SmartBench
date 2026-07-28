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
from smartbench.ir import FactKind, OperationKind, SemanticFact, SemanticIR, SemanticOperation


class ProtocolOrigin(str, Enum):
    """Provenance of a project-specific semantic mapping."""

    REFERENCE_USAGE = "reference_usage"
    PROJECT_READER = "project_reader"


@dataclass(frozen=True)
class ResourceProtocol:
    """A portable, project-scoped resource protocol hypothesis."""

    acquire_symbol: str
    resource_result_index: int
    cleanup_methods: tuple[str, ...]
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
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("protocol confidence must be between 0 and 1")

    @property
    def protocol_id(self) -> str:
        material = "|".join(
            (
                self.acquire_symbol,
                str(self.resource_result_index),
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
                "resource_binding": self.resource_binding,
                "cleanup_methods": list(self.protocol.cleanup_methods),
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

    def learn(self, ir: SemanticIR) -> list[ResourceProtocol]:
        cfg = ControlFlowGraph.from_ir(ir)
        operations = {operation.id: operation for operation in ir.operations}
        protocols: dict[tuple[str, int, tuple[str, ...]], ResourceProtocol] = {}
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
                methods = tuple(
                    sorted(
                        {
                            method
                            for operation in cleanup_operations
                            if (method := _method_name(operation))
                        }
                    )
                )
                if not methods:
                    continue
                key = (call.target, index, methods)
                protocols[key] = ResourceProtocol(
                    acquire_symbol=call.target,
                    resource_result_index=index,
                    cleanup_methods=methods,
                    origin=ProtocolOrigin.REFERENCE_USAGE,
                    confidence=1.0,
                )
        return [protocols[key] for key in sorted(protocols)]


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
        operations = {operation.id: operation for operation in ir.operations}
        ordered = sorted(ir.operations, key=_operation_order)

        for protocol in protocol_list:
            acquisitions = [
                operation
                for operation in ordered
                if operation.kind == OperationKind.CALL
                and operation.target == protocol.acquire_symbol
                and _is_primary_result_call(operation, operations)
            ]
            if not acquisitions:
                result.abstentions += 1
                result.unknown_reasons.append(
                    f"{protocol.protocol_id}: acquire symbol not present"
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

                cleanups = tuple(
                    operation
                    for operation in ordered
                    if operation.scope_id == acquire.scope_id
                    and operation.kind == OperationKind.DEFER
                    and _receiver_root(operation) == binding
                    and _method_name(operation) in protocol.cleanup_methods
                    and cfg.reachable(acquire.id, operation.id)
                )
                ownership_transfers = tuple(
                    operation
                    for operation in ordered
                    if operation.scope_id == acquire.scope_id
                    and _transfers_ownership(operation, binding)
                    and cfg.reachable(acquire.id, operation.id)
                )
                if ownership_transfers:
                    result.abstentions += 1
                    result.unknown_reasons.append(
                        f"{protocol.protocol_id}: ownership of {binding} is transferred "
                        "to the caller"
                    )
                    continue
                uses = [
                    operation
                    for operation in ordered
                    if operation.scope_id == acquire.scope_id
                    and operation.id not in {acquire.id, _host_id(acquire)}
                    and not _is_embedded_call(operation)
                    and operation not in cleanups
                    and _uses_binding(operation, binding)
                    and cfg.reachable(acquire.id, operation.id)
                ]
                if not uses:
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
                            cfg.reachable(acquire.id, cleanup.id)
                            and cfg.reachable(cleanup.id, use.id)
                            and cfg.dominates_between(acquire.id, cleanup.id, use.id)
                            for cleanup in cleanups
                        )
                    ),
                    None,
                )
                if first_unprotected is not None:
                    result.findings.append(
                        ResourceLifecycleFinding(
                            protocol=protocol,
                            resource_binding=binding,
                            acquire=acquire,
                            first_unprotected_use=first_unprotected,
                            cleanup_candidates=cleanups,
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
    receiver = str(operation.attributes.get("receiver", "")).strip()
    if not receiver and "." in operation.target:
        receiver = operation.target.rsplit(".", 1)[0]
    while receiver.startswith(("&", "*", "(")):
        receiver = receiver[1:].lstrip()
    return receiver.split(".", 1)[0].split("(", 1)[0].strip()


def _method_name(operation: SemanticOperation) -> str:
    return operation.target.rsplit(".", 1)[-1].strip() if operation.target else ""


def _uses_binding(operation: SemanticOperation, binding: str) -> bool:
    if binding in operation.operands:
        return True
    arguments = operation.attributes.get("arguments", ())
    if isinstance(arguments, (list, tuple)):
        for argument in arguments:
            text = str(argument)
            if text == binding or text.startswith(f"{binding}."):
                return True
    if operation.kind == OperationKind.RETURN:
        returned = [item.strip() for item in operation.value.split(",")]
        if binding in returned:
            # Returning the resource itself transfers ownership to the caller.
            return False
        return binding in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", operation.value)
    return False


def _transfers_ownership(operation: SemanticOperation, binding: str) -> bool:
    if operation.kind != OperationKind.RETURN:
        return False
    returned = [item.strip() for item in operation.value.split(",")]
    return binding in returned


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, str]:
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        operation.id,
    )
