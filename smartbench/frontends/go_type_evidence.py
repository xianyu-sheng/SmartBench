"""Conservative Go surface-type evidence over normalized operations.

This provider intentionally does not emulate ``go/types``.  It only preserves
types that are visible in source declarations and follows local selector and
assignment chains when every step is source-backed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    EvidenceRef,
    OperationKind,
    SemanticIR,
    SemanticOperation,
    TypeEvidence,
    TypeEvidenceRole,
    TypeEvidenceSource,
    normalize_type_name,
)

GO_TYPE_EVIDENCE_PROVIDER = "go.surface"
_SELECTOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass
class GoTypeEvidenceResult:
    evidence: list[TypeEvidence] = field(default_factory=list)
    files_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _TypeStatement:
    raw_type: str
    canonical_type: str
    source: TypeEvidenceSource
    evidence: tuple[EvidenceRef, ...]
    field_path: str = ""


@dataclass
class _FileSurface:
    file_path: str
    package: str
    imports: dict[str, str]
    fields: dict[str, dict[str, _TypeStatement]]

    @property
    def package_key(self) -> str:
        return f"{PurePosixPath(self.file_path).parent.as_posix()}::{self.package}"


class GoSurfaceTypeProvider:
    """Emit source-backed type evidence without whole-program type checking."""

    def provide(self, ir: SemanticIR) -> GoTypeEvidenceResult:
        result = GoTypeEvidenceResult()
        parser = get_parser("go")
        if parser is None:
            result.errors.append("tree-sitter Go parser is unavailable")
            return result

        surfaces: dict[str, _FileSurface] = {}
        for file_path, unit in sorted(ir.source_units.items()):
            if unit.language != "go" and not file_path.endswith(".go"):
                continue
            source = ir.read_source(file_path)
            if source is None:
                result.errors.append(f"unable to read Go source: {file_path}")
                continue
            try:
                root = parser.parse(source.encode("utf-8")).root_node
                surfaces[file_path] = _extract_surface(file_path, source, root)
                result.files_analyzed += 1
            except Exception as exc:  # pragma: no cover - defensive provider boundary
                result.errors.append(f"{file_path}: {exc}")

        package_fields = _merge_package_fields(surfaces)
        operations_by_scope: dict[str, list[SemanticOperation]] = {}
        functions: dict[
            tuple[str, str], list[tuple[SemanticOperation, _FileSurface]]
        ] = {}
        for operation in ir.operations:
            if operation.language != "go":
                continue
            if operation.kind == OperationKind.FUNCTION:
                package_key = surfaces.get(
                    operation.location.file_path, _EMPTY_SURFACE
                ).package_key
                function_surface = surfaces.get(
                    operation.location.file_path, _EMPTY_SURFACE
                )
                functions.setdefault((package_key, operation.target), []).append(
                    (operation, function_surface)
                )
            operations_by_scope.setdefault(operation.scope_id, []).append(operation)

        for scope_operations in operations_by_scope.values():
            environment: dict[str, _TypeStatement] = {}
            for operation in sorted(scope_operations, key=_operation_order):
                surface = surfaces.get(operation.location.file_path, _EMPTY_SURFACE)
                fields = package_fields.get(surface.package_key, {})
                if operation.kind == OperationKind.PARAMETER:
                    statement = _declared_operation_type(operation, surface)
                    if statement is not None and operation.target:
                        environment[operation.target] = statement
                        result.evidence.append(
                            _type_evidence(
                                operation,
                                TypeEvidenceRole.BINDING,
                                statement,
                                binding=operation.target,
                                position=_integer_attribute(operation, "position"),
                            )
                        )
                    continue
                if operation.kind == OperationKind.ASSIGN:
                    self._record_assignment(
                        operation,
                        surface,
                        fields,
                        environment,
                        result.evidence,
                    )
                    continue
                if operation.kind not in {
                    OperationKind.CALL,
                    OperationKind.DEFER,
                    OperationKind.SPAWN,
                }:
                    continue
                receiver = str(operation.attributes.get("receiver", "")).strip()
                statement = _resolve_expression(receiver, environment, fields)
                if statement is not None:
                    method = operation.target.rsplit(".", 1)[-1]
                    result.evidence.append(
                        _type_evidence(
                            operation,
                            TypeEvidenceRole.RECEIVER,
                            statement,
                            binding=receiver,
                            canonical_symbol=(
                                f"{statement.canonical_type}.{method}"
                                if statement.canonical_type and method
                                else ""
                            ),
                        )
                    )
                self._record_local_results(
                    operation,
                    surface,
                    functions,
                    result.evidence,
                )

        unique = {item.evidence_id: item for item in result.evidence}
        result.evidence = [unique[key] for key in sorted(unique)]
        return result

    @staticmethod
    def _record_assignment(
        operation: SemanticOperation,
        surface: _FileSurface,
        fields: dict[str, dict[str, _TypeStatement]],
        environment: dict[str, _TypeStatement],
        output: list[TypeEvidence],
    ) -> None:
        raw_bindings = operation.attributes.get("bindings", ())
        if not isinstance(raw_bindings, (list, tuple)):
            return
        bindings = [item for item in raw_bindings if isinstance(item, dict)]
        propagated = (
            _resolve_expression(operation.value.strip(), environment, fields)
            if len(bindings) == 1
            else None
        )
        for position, binding in enumerate(bindings):
            target = str(binding.get("target", "")).strip()
            if not target or not _SELECTOR.fullmatch(target):
                continue
            declared = str(binding.get("declared_type", "")).strip()
            inferred = str(binding.get("inferred_type", "")).strip()
            statement: _TypeStatement | None = None
            if declared or inferred:
                raw_type = declared or inferred
                statement = _TypeStatement(
                    raw_type=raw_type,
                    canonical_type=_canonicalize_type(raw_type, surface.imports),
                    source=TypeEvidenceSource.SURFACE_DECLARATION,
                    evidence=(operation.location,),
                )
            elif propagated is not None:
                statement = _TypeStatement(
                    raw_type=propagated.raw_type,
                    canonical_type=propagated.canonical_type,
                    source=TypeEvidenceSource.LOCAL_PROPAGATION,
                    evidence=_dedupe_refs((*propagated.evidence, operation.location)),
                    field_path=propagated.field_path,
                )
            if statement is None:
                continue
            environment[target] = statement
            output.append(
                _type_evidence(
                    operation,
                    TypeEvidenceRole.BINDING,
                    statement,
                    binding=target,
                    position=position,
                )
            )

    @staticmethod
    def _record_local_results(
        operation: SemanticOperation,
        surface: _FileSurface,
        functions: dict[
            tuple[str, str], list[tuple[SemanticOperation, _FileSurface]]
        ],
        output: list[TypeEvidence],
    ) -> None:
        if "." in operation.target:
            return
        matches = functions.get((surface.package_key, operation.target), ())
        if len(matches) != 1:
            return
        declaration, declaration_surface = matches[0]
        raw_types = declaration.attributes.get("return_types", ())
        if not isinstance(raw_types, (list, tuple)):
            return
        for position, raw_type in enumerate(raw_types):
            type_name = str(raw_type).strip()
            if not type_name:
                continue
            statement = _TypeStatement(
                raw_type=type_name,
                canonical_type=_canonicalize_type(
                    type_name, declaration_surface.imports
                ),
                source=TypeEvidenceSource.SURFACE_DECLARATION,
                evidence=(declaration.location, operation.location),
            )
            output.append(
                _type_evidence(
                    operation,
                    TypeEvidenceRole.RESULT,
                    statement,
                    position=position,
                )
            )


def _extract_surface(file_path: str, source: str, root: Any) -> _FileSurface:
    source_bytes = source.encode("utf-8")

    def text(node: Any | None) -> str:
        if node is None:
            return ""
        return source_bytes[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        )

    package = ""
    imports: dict[str, str] = {}
    for node in root.named_children:
        if node.type == "package_clause" and node.named_children:
            package = text(node.named_children[-1])
        if node.type != "import_declaration":
            continue
        for item in _descendants(node):
            if item.type != "import_spec":
                continue
            path = text(item.child_by_field_name("path")).strip('"`')
            if not path:
                continue
            name = text(item.child_by_field_name("name"))
            alias = name or PurePosixPath(path).name
            if alias not in {"_", "."}:
                imports[alias] = path

    fields: dict[str, dict[str, _TypeStatement]] = {}
    for node in root.named_children:
        if node.type != "type_declaration":
            continue
        for spec in node.named_children:
            if spec.type not in {"type_spec", "type_alias"}:
                continue
            name = text(spec.child_by_field_name("name"))
            type_node = spec.child_by_field_name("type")
            if not name or type_node is None or type_node.type != "struct_type":
                continue
            type_fields: dict[str, _TypeStatement] = {}
            for declaration in _descendants(type_node):
                if declaration.type != "field_declaration":
                    continue
                declared_type = text(declaration.child_by_field_name("type"))
                names = [
                    text(child)
                    for child in declaration.named_children
                    if child.type == "field_identifier"
                ]
                if not declared_type or not names:
                    continue
                ref = EvidenceRef(
                    file_path=file_path,
                    line_start=declaration.start_point[0] + 1,
                    line_end=declaration.end_point[0] + 1,
                    column_start=declaration.start_point[1],
                    column_end=declaration.end_point[1],
                    snippet=text(declaration),
                    source="go_type_evidence",
                )
                for field_name in names:
                    type_fields[field_name] = _TypeStatement(
                        raw_type=declared_type,
                        canonical_type=_canonicalize_type(declared_type, imports),
                        source=TypeEvidenceSource.SURFACE_FIELD,
                        evidence=(ref,),
                        field_path=field_name,
                    )
            fields[name] = type_fields
    return _FileSurface(file_path, package, imports, fields)


def _merge_package_fields(
    surfaces: dict[str, _FileSurface],
) -> dict[str, dict[str, dict[str, _TypeStatement]]]:
    merged: dict[str, dict[str, dict[str, _TypeStatement]]] = {}
    for surface in surfaces.values():
        package = merged.setdefault(surface.package_key, {})
        for type_name, fields in surface.fields.items():
            package.setdefault(type_name, {}).update(fields)
    return merged


def _declared_operation_type(
    operation: SemanticOperation,
    surface: _FileSurface,
) -> _TypeStatement | None:
    raw_type = str(operation.attributes.get("declared_type", "")).strip()
    if not raw_type:
        return None
    return _TypeStatement(
        raw_type=raw_type,
        canonical_type=_canonicalize_type(raw_type, surface.imports),
        source=TypeEvidenceSource.SURFACE_DECLARATION,
        evidence=(operation.location,),
    )


def _resolve_expression(
    expression: str,
    environment: dict[str, _TypeStatement],
    fields: dict[str, dict[str, _TypeStatement]],
) -> _TypeStatement | None:
    expression = expression.strip()
    while expression.startswith(("&", "*")):
        expression = expression[1:].strip()
    if not _SELECTOR.fullmatch(expression):
        return None
    parts = expression.split(".")
    statement = environment.get(parts[0])
    if statement is None:
        return None
    traversed: list[str] = []
    for field_name in parts[1:]:
        owner = _local_type_name(statement.raw_type)
        field = fields.get(owner, {}).get(field_name)
        if field is None:
            return None
        traversed.append(field_name)
        statement = _TypeStatement(
            raw_type=field.raw_type,
            canonical_type=field.canonical_type,
            source=TypeEvidenceSource.SURFACE_FIELD,
            evidence=_dedupe_refs((*statement.evidence, *field.evidence)),
            field_path=".".join(traversed),
        )
    return statement


def _canonicalize_type(raw_type: str, imports: dict[str, str]) -> str:
    normalized = normalize_type_name(raw_type)
    alias, separator, remainder = normalized.partition(".")
    if separator and alias in imports:
        return f"{imports[alias]}.{remainder}"
    return normalized


def _local_type_name(raw_type: str) -> str:
    normalized = normalize_type_name(raw_type)
    return normalized.rsplit(".", 1)[-1]


def _type_evidence(
    operation: SemanticOperation,
    role: TypeEvidenceRole,
    statement: _TypeStatement,
    *,
    binding: str = "",
    position: int = -1,
    canonical_symbol: str = "",
) -> TypeEvidence:
    attributes = {"surface_only": True}
    if statement.field_path:
        attributes["field_path"] = statement.field_path
    return TypeEvidence(
        operation_id=operation.id,
        role=role,
        type_name=statement.canonical_type or statement.raw_type,
        source=statement.source,
        provider=GO_TYPE_EVIDENCE_PROVIDER,
        binding=binding,
        position=position,
        canonical_symbol=canonical_symbol,
        confidence=1.0,
        evidence=_dedupe_refs((*statement.evidence, operation.location)),
        attributes=attributes,
    )


def _integer_attribute(operation: SemanticOperation, name: str) -> int:
    value = operation.attributes.get(name, -1)
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _dedupe_refs(values: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    unique: dict[tuple[str, int, int, str], EvidenceRef] = {}
    for item in values:
        key = (
            item.file_path,
            item.line_start,
            item.line_end or item.line_start,
            item.snippet,
        )
        unique.setdefault(key, item)
    return tuple(unique.values())


def _operation_order(operation: SemanticOperation) -> tuple[str, int, int, int, str]:
    priority = {
        OperationKind.PARAMETER: 0,
        OperationKind.ASSIGN: 1,
        OperationKind.CALL: 2,
        OperationKind.DEFER: 2,
        OperationKind.SPAWN: 2,
    }.get(operation.kind, 3)
    return (
        operation.location.file_path,
        operation.location.line_start,
        operation.location.column_start or 0,
        priority,
        operation.id,
    )


def _descendants(node: Any):
    yield node
    for child in node.named_children:
        yield from _descendants(child)


_EMPTY_SURFACE = _FileSurface("", "", {}, {})
