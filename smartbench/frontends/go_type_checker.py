"""Go type-checker evidence provider.

The surface provider (``go.surface``) only preserves types visible in the
analyzed repository's own declarations.  It deliberately does not emulate
``go/types``, so a receiver whose type lives in a dependency (an SDK response
object, ``net/http.Client``, ...) is either missing or only guessed at.

This provider closes that gap: it shells out to the bundled ``typeprobe``
helper (``tools/typeprobe``), which loads the target module with
``go/packages`` + ``go/types`` and resolves the true type of a symbol at a
given file:line.  The resolved types are emitted as ``TypeEvidence`` with
``TypeEvidenceSource.TYPE_CHECKER`` (rank 3), so consumers such as the
resource-lifecycle analyzer prefer them over surface evidence.

Degradation contract: if the Go toolchain or the probe binary is
unavailable, the provider records an error and emits nothing.  It must never
invent a type; missing evidence is the ``unknown``/``abstained`` path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from smartbench.frontends.go_type_evidence import (
    _operation_order,  # reuse the deterministic ordering helper
)
from smartbench.ir import (
    OperationKind,
    SemanticIR,
    SemanticOperation,
    TypeEvidence,
    TypeEvidenceRole,
    TypeEvidenceSource,
)

GO_TYPE_CHECKER_PROVIDER = "go.typechecker"
PROBE_ENV_VAR = "SMARTBENCH_GO_TYPEPROBE"
_DEFAULT_PROBE_RELATIVE = Path("tools/typeprobe/typeprobe")
_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_QUERIES_PER_BATCH = 2000
_PROBE_CHUNK_SIZE = 500


@dataclass
class GoTypeCheckerResult:
    evidence: list[TypeEvidence] = field(default_factory=list)
    files_analyzed: int = 0
    queries: int = 0
    errors: list[str] = field(default_factory=list)


def _find_probe_binary(project_root: str | Path | None = None) -> str | None:
    """Locate the typeprobe binary.

    Resolution order: ``SMARTBENCH_GO_TYPEPROBE`` env var, then
    ``<project_root>/tools/typeprobe/typeprobe``, then the repository
    checkout relative to this file (``../../tools/typeprobe/typeprobe``).
    """
    configured = os.environ.get(PROBE_ENV_VAR, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(Path(project_root) / _DEFAULT_PROBE_RELATIVE)
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / _DEFAULT_PROBE_RELATIVE)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _auto_build_probe() -> str | None:
    """Best-effort build of tools/typeprobe when the Go toolchain exists."""
    if shutil.which("go") is None:
        return None
    repo_root = Path(__file__).resolve().parents[2]
    source_dir = repo_root / "tools/typeprobe"
    if not (source_dir / "main.go").is_file():
        return None
    binary = source_dir / "typeprobe"
    try:
        result = subprocess.run(
            ["go", "build", "-buildvcs=false", "-o", str(binary), "."],
            cwd=str(source_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and binary.is_file() and os.access(binary, os.X_OK):
            return str(binary)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


class GoTypeCheckerProvider:
    """Emit ``TYPE_CHECKER`` type evidence for Go CALL receivers."""

    def __init__(
        self,
        probe_binary: str | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        project_root: str | Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._project_root = project_root
        self._probe_binary = probe_binary or _find_probe_binary(project_root)

    def provide(self, ir: SemanticIR) -> GoTypeCheckerResult:
        result = GoTypeCheckerResult()
        if self._probe_binary is None:
            result.errors.append(
                f"typeprobe binary unavailable (set {PROBE_ENV_VAR} or build tools/typeprobe)"
            )
            return result

        queries = self._collect_queries(ir)
        if not queries:
            return result
        result.queries = len(queries)

        by_query: dict[tuple[str, int, str], dict[str, Any]] = {}
        for offset in range(0, len(queries), _PROBE_CHUNK_SIZE):
            chunk = queries[offset : offset + _PROBE_CHUNK_SIZE]
            responses = self._run_probe(chunk)
            if responses is None:
                result.errors.append(f"typeprobe chunk failed (offset={offset})")
                continue
            by_query.update(
                {_query_key(q): response for q, response in zip(chunk, responses)}
            )
        for operation in sorted(ir.operations, key=_operation_order):
            if operation.kind != OperationKind.CALL or operation.language != "go":
                continue
            receiver = operation.attributes.get("receiver", "")
            if isinstance(receiver, str) and receiver.strip() and _probeable_receiver(receiver):
                response = by_query.get(
                    _query_key(
                        {"file": operation.location.file_path, "line": operation.location.line_start, "symbol": receiver}
                    )
                )
                evidence = _build_evidence(operation, receiver, TypeEvidenceRole.RECEIVER, response)
                if evidence is not None:
                    result.evidence.append(evidence)
                    result.files_analyzed += 1
            result_targets = operation.attributes.get("result_targets", [])
            if isinstance(result_targets, list):
                for target in result_targets:
                    if not isinstance(target, str) or not target.strip() or not _probeable_receiver(target):
                        continue
                    response = by_query.get(
                        _query_key(
                            {"file": operation.location.file_path, "line": operation.location.line_start, "symbol": target}
                        )
                    )
                    evidence = _build_evidence(operation, target, TypeEvidenceRole.RESULT, response)
                    if evidence is not None:
                        result.evidence.append(evidence)
                        result.files_analyzed += 1
        return result
    def _collect_queries(self, ir: SemanticIR) -> list[dict[str, Any]]:
        seen: set[tuple[str, int, str]] = set()
        queries: list[dict[str, Any]] = []
        for operation in sorted(ir.operations, key=_operation_order):
            if operation.kind != OperationKind.CALL or operation.language != "go":
                continue
            receiver = operation.attributes.get("receiver", "")
            if isinstance(receiver, str) and receiver.strip() and _probeable_receiver(receiver):
                key = (operation.location.file_path, operation.location.line_start, receiver)
                if key not in seen:
                    seen.add(key)
                    queries.append(
                        {"file": operation.location.file_path, "line": operation.location.line_start, "symbol": receiver}
                    )
            result_targets = operation.attributes.get("result_targets", [])
            if isinstance(result_targets, list):
                for target in result_targets:
                    if not isinstance(target, str) or not target.strip():
                        continue
                    if not _probeable_receiver(target):
                        continue
                    key = (operation.location.file_path, operation.location.line_start, target)
                    if key in seen:
                        continue
                    seen.add(key)
                    queries.append(
                        {"file": operation.location.file_path, "line": operation.location.line_start, "symbol": target}
                    )
        return queries

    def _run_probe(self, queries: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        import smartbench.subprocess_utils as subprocess_utils

        probe_binary = self._probe_binary
        if not probe_binary:
            return None
        payload = json.dumps(queries, ensure_ascii=False).encode("utf-8")
        try:
            completed = subprocess_utils.run_bounded(
                [probe_binary],
                cwd=str(self._project_root) if self._project_root is not None else None,
                timeout=self._timeout,
                input_text=payload.decode("utf-8"),
                max_stdout_bytes=512 * 1024,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return None
        if completed.returncode not in (0,):
            return None
        try:
            parsed = json.loads(completed.stdout)
        except (json.JSONDecodeError, ValueError):  # pragma: no cover
            return None
        if not isinstance(parsed, list):
            return None
        responses: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict):
                responses.append(item)
            else:  # pragma: no cover - defensive
                responses.append({})
        return responses


def _query_key(query: Mapping[str, Any]) -> tuple[str, int, str]:
    return (query["file"], int(query["line"]), query["symbol"])


def _build_evidence(
    operation: SemanticOperation,
    binding: str,
    role: TypeEvidenceRole,
    response: Mapping[str, Any] | None,
) -> TypeEvidence | None:
    """Build a TYPE_CHECKER evidence from a probe response, or None if the
    probe response is unusable (error, empty type, or a useless surface such
    as a package reference or signature)."""
    if response is None or response.get("error"):
        return None
    type_name = response.get("declared_type", "")
    if not type_name or _useless_probe_type(type_name):
        return None
    return TypeEvidence(
        operation_id=operation.id,
        role=role,
        type_name=type_name,
        source=TypeEvidenceSource.TYPE_CHECKER,
        provider=GO_TYPE_CHECKER_PROVIDER,
        binding=binding,
        canonical_symbol=_canonical_symbol(binding, type_name),
        confidence=1.0,
        evidence=(operation.location,),
        attributes={
            "has_close_method": bool(response.get("has_close_method")),
            "probe_object_kind": response.get("object_kind", ""),
        },
    )


def _useless_probe_type(type_name: str) -> bool:
    """True when a probe result is not a resource-object type."""
    stripped = type_name.strip()
    if not stripped or stripped == "invalid type":
        return True
    if stripped.startswith("("):  # multi-value signature "(A, B)"
        return True
    if "func(" in stripped or stripped.startswith("func "):
        return True
    if stripped.endswith("(type)"):
        return True
    return False


def _probeable_receiver(receiver: str) -> bool:
    """True when a receiver expression is worth probing.

    Supports:
    1. Simple identifiers: ``client``
    2. Selector chains: ``client.conn``
    3. Single call with field access: ``http.Get(url).Body``

    Rejects:
    - Multiple chained calls: ``Get().Do().Run()``
    - String literals and package references
    """
    stripped = receiver.strip()
    if not stripped:
        return False

    # Reject string literals and expressions with spaces (operators)
    if '"' in stripped or "'" in stripped:
        return False
    if " " in stripped and not _is_single_call_pattern(stripped):
        return False

    # No parentheses: simple identifier chain
    if "(" not in stripped and ")" not in stripped:
        parts = stripped.split(".")
        return all(part.isidentifier() for part in parts)

    # Has parentheses: check if it's a single call pattern
    return _is_single_call_pattern(stripped)


def _is_single_call_pattern(expr: str) -> bool:
    """Check if expression matches: pkg.Func(args).field or obj.method().field

    Examples that should return True:
    - http.Get(url).Body
    - client.Do(req).StatusCode
    - resp.Body.Read(buf)

    Examples that should return False:
    - Get().Do().Run() (multiple calls)
    - "string literal"
    - exec.Command("cmd", "arg") (no field access after call)
    """
    # Count opening parens - should be exactly 1 for single call
    if expr.count("(") != 1 or expr.count(")") != 1:
        return False

    # Find the parentheses positions
    open_idx = expr.find("(")
    close_idx = expr.find(")")

    # Closing paren must come after opening
    if close_idx <= open_idx:
        return False

    # Must have content before opening paren (function name)
    before_call = expr[:open_idx]
    if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
        return False

    # Check what comes after the closing paren
    after_call = expr[close_idx + 1:]

    # If nothing after, this is just a function call without field access
    # Still probeable if the function name itself is a selector
    if not after_call:
        return "." in before_call

    # Must start with a dot for field access
    if not after_call.startswith("."):
        return False

    # The field access part should be a valid identifier chain
    field_chain = after_call[1:]  # Skip the leading dot
    if not field_chain:
        return False

    # Check if field chain is valid identifiers separated by dots
    parts = field_chain.split(".")
    return all(part.isidentifier() for part in parts)


def _canonical_symbol(receiver: str, type_name: str) -> str:
    """Best-effort canonical acquire symbol: <type>.<last selector segment>."""
    if "." not in receiver:
        return ""
    method = receiver.rsplit(".", 1)[1]
    if not method or method.startswith("("):
        return ""
    base = type_name.strip("*").strip("[]").strip("()")
    if not base:
        return ""
    return f"{base}.{method}"


def probe_available() -> bool:
    """True when a probe binary can be found or built on this machine."""
    if _find_probe_binary() is not None:
        return True
    return _auto_build_probe() is not None
