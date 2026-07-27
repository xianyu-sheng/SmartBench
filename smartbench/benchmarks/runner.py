"""Safe, deterministic runner for repository snapshot benchmarks."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    register_all_adapters,
)
from smartbench.path_safety import read_text_bounded

BENCHMARK_SCHEMA_VERSION = "smartbench.benchmarks/v1"


class BenchmarkConfigError(ValueError):
    """Raised when a benchmark manifest is invalid."""


@dataclass(frozen=True)
class BenchmarkSnapshot:
    label: str
    path: Path
    min_findings: int = 0
    max_findings: int | None = None
    expected_rule_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    language: str
    state_rule_paths: tuple[Path, ...]
    rule_ids: tuple[str, ...]
    snapshots: tuple[BenchmarkSnapshot, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkSnapshotResult:
    case_id: str
    label: str
    path: str
    findings: int
    finding_rule_ids: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    passed: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "label": self.label,
            "path": self.path,
            "findings": self.findings,
            "finding_rule_ids": self.finding_rule_ids,
            "metadata": self.metadata,
            "errors": self.errors,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
        }


@dataclass
class BenchmarkReport:
    schema_version: str = BENCHMARK_SCHEMA_VERSION
    results: list[BenchmarkSnapshotResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(result.passed for result in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "snapshots": [result.to_dict() for result in self.results],
            "summary": {
                "total": len(self.results),
                "passed": sum(result.passed for result in self.results),
                "failed": sum(not result.passed for result in self.results),
            },
        }


def load_benchmark_manifest(file_path: Path) -> list[BenchmarkCase]:
    """Load a bounded, versioned benchmark manifest."""
    source = read_text_bounded(file_path, 1024 * 1024)
    if source is None:
        raise BenchmarkConfigError(f"cannot read benchmark manifest: {file_path}")
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise BenchmarkConfigError(f"invalid YAML in {file_path}: {exc}") from exc
    root = _mapping(document, "document")
    _reject_unknown(root, {"version", "cases"}, "document")
    if root.get("version") != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkConfigError(
            f"unsupported benchmark version {root.get('version')!r}; "
            f"expected {BENCHMARK_SCHEMA_VERSION!r}"
        )
    raw_cases = root.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BenchmarkConfigError("document.cases must be a non-empty list")
    base = file_path.expanduser().resolve().parent
    cases = [_parse_case(value, index, base) for index, value in enumerate(raw_cases)]
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise BenchmarkConfigError("benchmark case IDs must be unique")
    return cases


class BenchmarkRunner:
    """Run each declared snapshot through the same unified engine."""

    def __init__(self) -> None:
        adapters = AdapterRegistry()
        register_all_adapters(adapters)
        self.engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())

    def run(self, cases: list[BenchmarkCase]) -> BenchmarkReport:
        report = BenchmarkReport()
        for case in cases:
            for snapshot in case.snapshots:
                report.results.append(self._run_snapshot(case, snapshot))
        return report

    def _run_snapshot(
        self,
        case: BenchmarkCase,
        snapshot: BenchmarkSnapshot,
    ) -> BenchmarkSnapshotResult:
        start = time.time()
        config = UnifiedDiagnosticConfig(
            use_static_rules=False,
            languages=[case.language],
            rule_ids=list(case.rule_ids) or None,
            state_rule_paths=list(case.state_rule_paths),
            min_confidence=0.0,
            build_evidence_packs=True,
        )
        result = self.engine.diagnose(snapshot.path, config)
        finding_ids = [finding.rule_id for finding in result.findings]
        actual_ids = set(finding_ids)
        meets_min = len(finding_ids) >= snapshot.min_findings
        meets_max = snapshot.max_findings is None or len(finding_ids) <= snapshot.max_findings
        matches_rules = (
            not snapshot.expected_rule_ids
            or snapshot.expected_rule_ids.issubset(actual_ids)
        )
        passed = not result.errors and meets_min and meets_max and matches_rules
        return BenchmarkSnapshotResult(
            case_id=case.case_id,
            label=snapshot.label,
            path=str(snapshot.path),
            findings=len(finding_ids),
            finding_rule_ids=finding_ids,
            metadata=dict(case.metadata),
            errors=list(result.errors),
            passed=passed,
            duration_ms=int((time.time() - start) * 1000),
        )


def _parse_case(value: Any, index: int, base: Path) -> BenchmarkCase:
    path = f"cases[{index}]"
    case = _mapping(value, path)
    _reject_unknown(
        case,
        {"id", "language", "state_rules", "rules", "snapshots", "metadata"},
        path,
    )
    case_id = _non_empty_string(case.get("id"), f"{path}.id")
    language = _non_empty_string(case.get("language"), f"{path}.language").lower()
    rule_paths = tuple(
        _resolve_path(base, item, f"{path}.state_rules")
        for item in _string_list(case.get("state_rules", []), f"{path}.state_rules")
    )
    rule_ids = tuple(_string_list(case.get("rules", []), f"{path}.rules"))
    raw_snapshots = case.get("snapshots")
    if not isinstance(raw_snapshots, list) or not raw_snapshots:
        raise BenchmarkConfigError(f"{path}.snapshots must be a non-empty list")
    snapshots = tuple(
        _parse_snapshot(item, index, base, case_id)
        for index, item in enumerate(raw_snapshots)
    )
    metadata = _parse_metadata(case.get("metadata", {}), f"{path}.metadata")
    return BenchmarkCase(case_id, language, rule_paths, rule_ids, snapshots, metadata)


def _parse_snapshot(value: Any, index: int, base: Path, case_id: str) -> BenchmarkSnapshot:
    path = f"cases[{case_id}].snapshots[{index}]"
    snapshot = _mapping(value, path)
    _reject_unknown(
        snapshot,
        {"label", "path", "min_findings", "max_findings", "expected_rule_ids"},
        path,
    )
    label = _non_empty_string(snapshot.get("label"), f"{path}.label")
    project = _resolve_path(base, snapshot.get("path"), f"{path}.path")
    min_findings = _non_negative_int(snapshot.get("min_findings", 0), f"{path}.min_findings")
    max_value = snapshot.get("max_findings")
    max_findings = (
        None if max_value is None
        else _non_negative_int(max_value, f"{path}.max_findings")
    )
    if max_findings is not None and min_findings > max_findings:
        raise BenchmarkConfigError(f"{path}: min_findings cannot exceed max_findings")
    expected_ids = frozenset(
        _string_list(snapshot.get("expected_rule_ids", []), f"{path}.expected_rule_ids")
    )
    return BenchmarkSnapshot(label, project, min_findings, max_findings, expected_ids)


def _resolve_path(base: Path, value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkConfigError(f"{path} must be a non-empty path")
    return (base / value).expanduser().resolve() if not Path(value).is_absolute() else Path(value).expanduser().resolve()


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkConfigError(f"{path} must be a mapping")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BenchmarkConfigError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, path: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise BenchmarkConfigError(f"{path} must be a string or list of non-empty strings")
    return [item.strip() for item in value]


def _parse_metadata(value: Any, path: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BenchmarkConfigError(f"{path} must be a mapping")
    if not all(isinstance(key, str) and key.strip() for key in value):
        raise BenchmarkConfigError(f"{path} keys must be non-empty strings")
    return {key.strip(): item for key, item in value.items()}


def _non_negative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkConfigError(f"{path} must be a non-negative integer")
    return value
