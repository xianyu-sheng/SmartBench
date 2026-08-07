"""Conservative Python surface-type evidence over normalized operations.

This provider mirrors ``GoSurfaceTypeProvider``: it never attempts full
type inference.  It preserves types that are visible in source annotations
(variable annotations, parameter annotations, return annotations) and
binds them to CALL receiver operations so consumers such as the
resource-lifecycle analyzer can decide whether a receiver is a
closeable resource.

Sources used, in increasing strength:
- LOCAL_PROPAGATION: receiver bound from a local assignment chain
  (``f = open(...)`` / ``f = g`` where ``g`` has an annotation).
- SURFACE_DECLARATION: receiver is an annotated name (variable
  annotation ``f: BinaryIO``, parameter annotation, or return
  annotation of the callee).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Optional

from smartbench.ir import (
    OperationKind,
    SemanticIR,
    TypeEvidence,
    TypeEvidenceRole,
    TypeEvidenceSource,
    normalize_type_name,
)

PYTHON_TYPE_EVIDENCE_PROVIDER = "python.surface"

# A small set of well-known stdlib names that name resources; these are
# only used to keep the provider honest about "resource" membership when
# no annotation is present at all (purely heuristic, low confidence).
_STDLIB_RESOURCE_PREFIXES = (
    "open",
    "io.",
    "socket.",
    "tempfile.",
    "pathlib.",
    "tarfile.",
    "zipfile.",
    "gzip.",
    "bz2.",
    "lzma.",
    "http.client.",
    "urllib.",
    "sqlite3.",
    "subprocess.Popen",
)


@dataclass
class PythonTypeEvidenceResult:
    evidence: list[TypeEvidence] = field(default_factory=list)
    files_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


class PythonSurfaceTypeProvider:
    """Emit source-backed type evidence for Python CALL receivers."""

    def provide(self, ir: SemanticIR) -> PythonTypeEvidenceResult:
        result = PythonTypeEvidenceResult()
        # One annotation map per file: name -> canonical type string.
        annotations: dict[str, dict[str, str]] = {}
        for file_path, unit in sorted(ir.source_units.items()):
            if unit.language != "python" and not file_path.endswith(".py"):
                continue
            source = ir.read_source(file_path)
            if source is None:
                result.errors.append(f"unable to read Python source: {file_path}")
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                result.errors.append(f"python parse error {file_path}: {exc}")
                continue
            annotations[file_path] = _collect_annotations(tree)
            result.files_analyzed += 1

        # Walk CALL operations and bind receivers to known annotations.
        for operation in ir.operations:
            if operation.kind != OperationKind.CALL:
                continue
            receiver = (operation.attributes or {}).get("receiver", "")
            if not receiver or "." in receiver:
                continue  # only simple local names, skip attribute chains
            evidence = self._evidence_for_receiver(
                operation.id, receiver, operation, annotations
            )
            if evidence is not None:
                result.evidence.append(evidence)
        return result

    def _evidence_for_receiver(
        self,
        operation_id: str,
        receiver: str,
        operation: Any,
        annotations: dict[str, dict[str, str]],
    ) -> Optional[TypeEvidence]:
        """Produce RECEIVER type evidence for a simple local receiver name."""
        location = getattr(operation, "location", None)
        file_path = getattr(location, "file_path", "") if location else ""
        if not file_path or file_path not in annotations:
            return None
        type_name = annotations[file_path].get(receiver, "")
        if not type_name:
            return None
        source = (
            TypeEvidenceSource.SURFACE_DECLARATION
            if _looks_annotated(annotations[file_path], receiver)
            else TypeEvidenceSource.LOCAL_PROPAGATION
        )
        return TypeEvidence(
            operation_id=operation_id,
            role=TypeEvidenceRole.RECEIVER,
            type_name=type_name,
            source=source,
            provider=PYTHON_TYPE_EVIDENCE_PROVIDER,
            binding=receiver,
            position=0,
            canonical_symbol=normalize_type_name(type_name),
            confidence=0.9,
            evidence=(),
        )


def _looks_annotated(names: dict[str, str], name: str) -> bool:
    """Heuristic: annotation maps built from annotations are SURFACE.

    We keep a single map per file; entries that came from an explicit
    annotation are indistinguishable from propagated ones here.  Because
    all entries originate from source annotations in ``_collect_annotations``,
    we treat every hit as a declaration-grade signal when the type name is
    a capitalized or dotted name (a real type), and fall back to
    propagation otherwise.
    """
    type_name = names.get(name, "")
    return bool(type_name) and (type_name[0].isupper() or "." in type_name)


def _collect_annotations(tree: ast.Module) -> dict[str, str]:
    """Collect name -> type from annotations in one module.

    Sources, in order of preference (later overwrites earlier):
      1. module/class-level annotated assignments: ``f: BinaryIO = ...``
      2. parameter annotations: ``def f(handle: BinaryIO)``
      3. return annotations propagated to local call targets:
         ``def make() -> BinaryIO`` then ``x = make()`` → x: BinaryIO
      4. function-level annotated assignments
    """
    annotations: dict[str, str] = {}
    return_types: dict[str, str] = {}  # function name -> return annotation

    # First pass: annotated assignments and return annotations.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                return_types[node.name] = _unparse_annotation(node.returns)
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    annotations[stmt.target.id] = _unparse_annotation(stmt.annotation)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            annotations[node.target.id] = _unparse_annotation(node.annotation)
        elif isinstance(node, ast.arg):
            if node.annotation is not None:
                annotations[node.arg] = _unparse_annotation(node.annotation)

    # Second pass: propagate return annotations through local call targets.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        callee = node.value.func
        func_name = callee.id if isinstance(callee, ast.Name) else ""
        if func_name and func_name in return_types:
            annotations[target.id] = return_types[func_name]
    return annotations


def _unparse_annotation(node: ast.AST) -> str:
    """Render an annotation node to a canonical dotted name."""
    try:
        return ast.unparse(node).strip()
    except Exception:
        return ""
