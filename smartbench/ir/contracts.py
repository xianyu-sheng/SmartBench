"""Typed contracts carried by interprocedural SemanticIR operations.

The operation model intentionally stays small, but its core interprocedural
fields must not devolve into language-specific, undocumented dictionaries.
This module provides immutable views and validation for the shared attributes
emitted by language frontends.  Unknown types remain strings: the contract
defines shape and provenance, not a pretend universal type checker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from smartbench.ir.operations import OperationKind, SemanticOperation

CONTRACT_SCHEMA_VERSION = "semantic-ir/contracts/v1"

_CALL_KINDS = frozenset({OperationKind.CALL, OperationKind.SPAWN, OperationKind.DEFER})


@dataclass(frozen=True)
class FunctionContract:
    """Signature metadata attached to a ``FUNCTION`` operation."""

    symbol_name: str
    qualified_name: str
    namespace: str
    receiver_type: str
    return_types: tuple[str, ...]

    @classmethod
    def from_operation(cls, operation: SemanticOperation) -> "FunctionContract":
        attributes = operation.attributes
        return cls(
            symbol_name=str(attributes.get("symbol_name", operation.target)),
            qualified_name=str(attributes.get("qualified_name", operation.target)),
            namespace=str(attributes.get("namespace", "")),
            receiver_type=str(attributes.get("receiver_type", "")),
            return_types=_string_tuple(attributes.get("return_types", ())),
        )


@dataclass(frozen=True)
class ParameterContract:
    """Parameter position and surface type metadata."""

    name: str
    position: int
    declared_type: str
    parameter_kind: str
    receiver: bool

    @classmethod
    def from_operation(cls, operation: SemanticOperation) -> "ParameterContract":
        attributes = operation.attributes
        position = attributes.get("position", 0)
        if isinstance(position, bool):
            position = 0
        return cls(
            name=operation.target,
            position=int(position),
            declared_type=str(attributes.get("declared_type", operation.value)),
            parameter_kind=str(attributes.get("parameter_kind", "positional")),
            receiver=bool(attributes.get("receiver", False)),
        )


@dataclass(frozen=True)
class BindingContract:
    """One assignment target and its declared/inferred surface types."""

    target: str
    declared_type: str = ""
    inferred_type: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "BindingContract":
        if not isinstance(value, Mapping):
            raise ValueError("binding must be an object")
        return cls(
            target=str(value.get("target", "")),
            declared_type=str(value.get("declared_type", "")),
            inferred_type=str(value.get("inferred_type", "")),
        )


@dataclass(frozen=True)
class CallContract:
    """Call-site arguments, receiver expression, and result bindings."""

    target: str
    receiver: str
    arguments: tuple[str, ...]
    argument_names: tuple[str, ...]
    result_targets: tuple[str, ...]
    host_operation: str = ""

    @classmethod
    def from_operation(cls, operation: SemanticOperation) -> "CallContract":
        attributes = operation.attributes
        return cls(
            target=operation.target,
            receiver=str(attributes.get("receiver", "")),
            arguments=_string_tuple(attributes.get("arguments", ())),
            argument_names=_string_tuple(attributes.get("argument_names", ())),
            result_targets=_string_tuple(attributes.get("result_targets", ())),
            host_operation=str(attributes.get("host_operation", "")),
        )


def validate_operation_contract(operation: SemanticOperation) -> tuple[str, ...]:
    """Return deterministic schema violations for one operation.

    Validation is deliberately structural.  Empty types are valid and mean
    that the frontend could not prove a type; malformed shapes are errors.
    """
    attributes = operation.attributes
    errors: list[str] = []
    if operation.kind == OperationKind.FUNCTION:
        _require_string_list(attributes, "return_types", errors)
        if not str(attributes.get("symbol_name", operation.target)):
            errors.append("symbol_name must be non-empty")
        if not str(attributes.get("qualified_name", operation.target)):
            errors.append("qualified_name must be non-empty")
    elif operation.kind == OperationKind.PARAMETER:
        position = attributes.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            errors.append("position must be an integer")
        elif position < -1:
            errors.append("position must be >= -1")
        if not isinstance(attributes.get("declared_type", operation.value), str):
            errors.append("declared_type must be a string")
        if not isinstance(attributes.get("parameter_kind"), str):
            errors.append("parameter_kind must be a string")
        if not isinstance(attributes.get("receiver"), bool):
            errors.append("receiver must be a boolean")
    elif operation.kind == OperationKind.ASSIGN:
        bindings = attributes.get("bindings")
        if not isinstance(bindings, list):
            errors.append("bindings must be a list")
        else:
            for index, binding in enumerate(bindings):
                try:
                    parsed = BindingContract.from_value(binding)
                except ValueError as exc:
                    errors.append(f"bindings[{index}]: {exc}")
                    continue
                if not parsed.target:
                    errors.append(f"bindings[{index}].target must be non-empty")
    elif operation.kind in _CALL_KINDS:
        _require_string_list(attributes, "arguments", errors)
        _require_string_list(attributes, "argument_names", errors)
        _require_string_list(attributes, "result_targets", errors)
        arguments = attributes.get("arguments")
        argument_names = attributes.get("argument_names")
        if isinstance(arguments, list) and isinstance(argument_names, list):
            if len(arguments) != len(argument_names):
                errors.append("argument_names must align with arguments")
        if not isinstance(attributes.get("receiver", ""), str):
            errors.append("receiver must be a string")
        if not isinstance(attributes.get("host_operation", ""), str):
            errors.append("host_operation must be a string")
    elif operation.kind == OperationKind.RETURN:
        _require_string_list(attributes, "values", errors)
    return tuple(errors)


def validate_semantic_ir(
    operations: Sequence[SemanticOperation],
) -> tuple[str, ...]:
    """Validate all contract-bearing operations in stable source order."""
    errors: list[str] = []
    ordered = sorted(
        operations,
        key=lambda operation: (
            operation.location.file_path,
            operation.location.line_start,
            operation.location.column_start or 0,
            operation.id,
        ),
    )
    for operation in ordered:
        for error in validate_operation_contract(operation):
            errors.append(f"{operation.id}: {error}")
    return tuple(errors)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _require_string_list(
    attributes: Mapping[str, Any],
    key: str,
    errors: list[str],
) -> None:
    value = attributes.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be a list")
        return
    if any(not isinstance(item, str) for item in value):
        errors.append(f"{key} entries must be strings")
