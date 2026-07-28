"""Blind cross-file resource protocol transfer experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocolMiner
from smartbench.benchmarks import load_benchmark_manifest
from smartbench.core import AdapterRegistry, register_all_adapters

BLIND_MANIFEST_VERSION = "smartbench.experiments/project-reader-blind/v1"


@dataclass(frozen=True)
class BlindSourceFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class BlindCaseSpec:
    benchmark_case_id: str
    source_repository: str
    excluded_target_paths: tuple[str, ...]
    expected_exact_before_findings: int
    expected_shape_before_findings: int
    reference_path: Path | None = None
    source_commit: str = ""
    source_files: tuple[BlindSourceFile, ...] = ()
    unsupported_reason: str = ""


@dataclass(frozen=True)
class BlindCaseResult:
    case_id: str
    source_repository: str
    reference_available: bool
    unsupported_reason: str
    target_paths_excluded: bool
    source_hashes_verified: bool
    exact_protocols: tuple[dict[str, object], ...]
    shape_protocols: tuple[dict[str, object], ...]
    exact_before_findings: int
    shape_before_findings: int
    shape_after_findings: int
    shape_before_abstentions: int
    negative_findings: int
    verification: dict[str, bool]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "source_repository": self.source_repository,
            "reference_available": self.reference_available,
            "unsupported_reason": self.unsupported_reason,
            "target_paths_excluded": self.target_paths_excluded,
            "source_hashes_verified": self.source_hashes_verified,
            "exact_protocols": list(self.exact_protocols),
            "shape_protocols": list(self.shape_protocols),
            "exact_before_findings": self.exact_before_findings,
            "shape_before_findings": self.shape_before_findings,
            "shape_after_findings": self.shape_after_findings,
            "shape_before_abstentions": self.shape_before_abstentions,
            "negative_findings": self.negative_findings,
            "verification": dict(self.verification),
            "passed": self.passed,
        }


@dataclass
class BlindExperimentReport:
    cases: list[BlindCaseResult] = field(default_factory=list)
    independent_negative_findings: int = 0
    independent_negative_abstentions: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            bool(self.cases)
            and all(case.passed for case in self.cases)
            and self.independent_negative_findings == 0
            and not self.errors
        )

    @property
    def shape_detected_cases(self) -> int:
        return sum(case.shape_before_findings > 0 for case in self.cases)

    def to_dict(self) -> dict[str, object]:
        case_count = len(self.cases)
        coverage = self.shape_detected_cases / case_count if case_count else 0.0
        return {
            "schema_version": "smartbench.experiments/project-reader-blind/v1",
            "experiment_type": "blind-cross-file-protocol-transfer",
            "passed": self.passed,
            "decision": "partial" if 0.0 < coverage < 1.0 else (
                "supported" if coverage == 1.0 else "unsupported"
            ),
            "cases": [case.to_dict() for case in self.cases],
            "independent_negatives": {
                "findings": self.independent_negative_findings,
                "abstentions": self.independent_negative_abstentions,
            },
            "errors": list(self.errors),
            "summary": {
                "cases": case_count,
                "reference_available": sum(
                    case.reference_available for case in self.cases
                ),
                "exact_before_detected": sum(
                    case.exact_before_findings > 0 for case in self.cases
                ),
                "shape_before_detected": self.shape_detected_cases,
                "shape_after_clean": sum(
                    case.shape_before_findings > 0
                    and case.shape_after_findings == 0
                    for case in self.cases
                ),
                "diagnostic_coverage": round(coverage, 4),
            },
            "limitations": [
                "Reference files come from pinned current project revisions, not the historical bug revisions.",
                "The target bug files and fixes are excluded from every reference inventory.",
                "Unsupported cases remain abstentions; no model guess is upgraded to evidence.",
                "Method-shape transfer lacks receiver type proof and therefore remains partial evidence.",
                "No finding authorizes an upstream issue or pull request.",
            ],
        }


def load_blind_manifest(path: Path) -> list[BlindCaseSpec]:
    manifest_path = path.expanduser().resolve()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != BLIND_MANIFEST_VERSION:
        raise ValueError(f"unsupported blind manifest: {manifest_path}")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("blind manifest cases must be a non-empty list")
    cases: list[BlindCaseSpec] = []
    seen: set[str] = set()
    for index, value in enumerate(raw_cases):
        if not isinstance(value, dict):
            raise ValueError(f"blind case #{index + 1} must be an object")
        case_id = _required_string(value, "benchmark_case_id")
        if case_id in seen:
            raise ValueError(f"duplicate blind case: {case_id}")
        seen.add(case_id)
        reference_raw = value.get("reference_path")
        reference_path = None
        if reference_raw is not None:
            if not isinstance(reference_raw, str) or not reference_raw.strip():
                raise ValueError(f"{case_id}: reference_path must be a string")
            reference_path = (manifest_path.parent / reference_raw).resolve()
            if not reference_path.is_dir():
                raise ValueError(f"{case_id}: reference path does not exist")
        excluded = _string_tuple(value.get("excluded_target_paths", ()))
        if not excluded:
            raise ValueError(f"{case_id}: excluded_target_paths must not be empty")
        source_files_raw = value.get("source_files", [])
        if not isinstance(source_files_raw, list):
            raise ValueError(f"{case_id}: source_files must be a list")
        source_files = tuple(
            BlindSourceFile(
                path=_required_string(item, "path"),
                sha256=_required_string(item, "sha256"),
            )
            for item in source_files_raw
            if isinstance(item, dict)
        )
        unsupported_reason = str(value.get("unsupported_reason", "")).strip()
        if reference_path is None and not unsupported_reason:
            raise ValueError(f"{case_id}: missing reference requires a reason")
        cases.append(
            BlindCaseSpec(
                benchmark_case_id=case_id,
                source_repository=_required_string(value, "source_repository"),
                excluded_target_paths=excluded,
                expected_exact_before_findings=_non_negative_int(
                    value, "expected_exact_before_findings"
                ),
                expected_shape_before_findings=_non_negative_int(
                    value, "expected_shape_before_findings"
                ),
                reference_path=reference_path,
                source_commit=str(value.get("source_commit", "")).strip(),
                source_files=source_files,
                unsupported_reason=unsupported_reason,
            )
        )
    return cases


def run_blind_project_reader_experiment(
    benchmark_manifest: Path,
    blind_manifest: Path,
    *,
    negative_path: Path | None = None,
) -> BlindExperimentReport:
    benchmark_cases = {
        case.case_id: case
        for case in load_benchmark_manifest(benchmark_manifest.expanduser().resolve())
    }
    specs = load_blind_manifest(blind_manifest)
    registry = AdapterRegistry()
    register_all_adapters(registry)
    analyzer = ResourceLifecycleAnalyzer()
    miner = ResourceProtocolMiner()
    report = BlindExperimentReport()
    all_shape_protocols = {}
    negative_ir = None
    if negative_path is not None:
        go_adapter = registry.get_adapter_for_language("go")
        if go_adapter is None:
            report.errors.append("independent negatives: no Go adapter")
        else:
            negative_ir = go_adapter.parse_semantic_project(negative_path.resolve())

    for spec in specs:
        benchmark_case = benchmark_cases.get(spec.benchmark_case_id)
        if benchmark_case is None:
            report.errors.append(f"{spec.benchmark_case_id}: benchmark case not found")
            continue
        adapter = registry.get_adapter_for_language(benchmark_case.language)
        if adapter is None:
            report.errors.append(
                f"{spec.benchmark_case_id}: no adapter for {benchmark_case.language}"
            )
            continue
        snapshots = {snapshot.label: snapshot for snapshot in benchmark_case.snapshots}
        before = snapshots.get("before")
        after = snapshots.get("after")
        if before is None or after is None:
            report.errors.append(
                f"{spec.benchmark_case_id}: before/after snapshots are required"
            )
            continue
        before_ir = adapter.parse_semantic_project(before.path)
        after_ir = adapter.parse_semantic_project(after.path)
        target_excluded = _target_paths_excluded(spec)
        hashes_verified = _source_hashes_verified(spec)
        if spec.reference_path is None:
            exact_protocols = ()
            shape_protocols = ()
        else:
            reference_ir = adapter.parse_semantic_project(spec.reference_path)
            exact_protocols = tuple(miner.learn(reference_ir))
            shape_protocols = tuple(
                miner.learn(reference_ir, generalize_method_shapes=True)
            )
        for protocol in shape_protocols:
            all_shape_protocols[protocol.protocol_id] = protocol

        exact_before = analyzer.analyze(before_ir, exact_protocols)
        shape_before = analyzer.analyze(before_ir, shape_protocols)
        shape_after = analyzer.analyze(after_ir, shape_protocols)
        negative_findings = 0
        if negative_ir is not None:
            negative_findings = len(
                analyzer.analyze(negative_ir, shape_protocols).findings
            )
        path_witness = bool(shape_before.findings) and all(
            finding.to_fact().attributes.get("proof")
            == "cfg_dominance_between_acquire_and_use"
            for finding in shape_before.findings
        )
        historical_reference = bool(benchmark_case.metadata.get("issue_url"))
        before_after = bool(shape_before.findings) and not shape_after.findings
        passed = (
            target_excluded
            and hashes_verified
            and len(exact_before.findings)
            == spec.expected_exact_before_findings
            and len(shape_before.findings)
            == spec.expected_shape_before_findings
            and not shape_after.findings
            and negative_findings == 0
        )
        report.cases.append(
            BlindCaseResult(
                case_id=spec.benchmark_case_id,
                source_repository=spec.source_repository,
                reference_available=spec.reference_path is not None,
                unsupported_reason=spec.unsupported_reason,
                target_paths_excluded=target_excluded,
                source_hashes_verified=hashes_verified,
                exact_protocols=tuple(
                    _protocol_dict(protocol) for protocol in exact_protocols
                ),
                shape_protocols=tuple(
                    _protocol_dict(protocol) for protocol in shape_protocols
                ),
                exact_before_findings=len(exact_before.findings),
                shape_before_findings=len(shape_before.findings),
                shape_after_findings=len(shape_after.findings),
                shape_before_abstentions=shape_before.abstentions,
                negative_findings=negative_findings,
                verification={
                    "before_after_regression": before_after,
                    "deterministic_path_witness": path_witness,
                    "historical_change_reference": historical_reference,
                },
                passed=passed,
            )
        )

    if negative_ir is not None:
        negative_result = analyzer.analyze(
            negative_ir,
            sorted(all_shape_protocols.values(), key=lambda item: item.protocol_id),
        )
        report.independent_negative_findings = len(negative_result.findings)
        report.independent_negative_abstentions = negative_result.abstentions
    return report


def _target_paths_excluded(spec: BlindCaseSpec) -> bool:
    if spec.reference_path is None:
        return True
    present = {
        path.relative_to(spec.reference_path).as_posix()
        for path in spec.reference_path.rglob("*")
        if path.is_file()
    }
    return not any(target in present for target in spec.excluded_target_paths)


def _source_hashes_verified(spec: BlindCaseSpec) -> bool:
    if spec.reference_path is None:
        return True
    if not spec.source_files:
        return False
    for source in spec.source_files:
        path = (spec.reference_path / source.path).resolve()
        try:
            path.relative_to(spec.reference_path)
        except ValueError:
            return False
        if not path.is_file():
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.sha256:
            return False
    return True


def _protocol_dict(protocol: object) -> dict[str, object]:
    return {
        "protocol_id": getattr(protocol, "protocol_id"),
        "acquire_symbol": getattr(protocol, "acquire_symbol"),
        "acquire_match_mode": getattr(protocol, "acquire_match_mode").value,
        "resource_result_index": getattr(protocol, "resource_result_index"),
        "resource_member_path": getattr(protocol, "resource_member_path"),
        "cleanup_methods": list(getattr(protocol, "cleanup_methods")),
    }


def _required_string(value: Mapping[str, Any], field_name: str) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return item.strip()


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _non_negative_int(value: Mapping[str, Any], field_name: str) -> int:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return item


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the blind cross-file ProjectReader protocol experiment."
    )
    parser.add_argument("--benchmark-manifest", type=Path, required=True)
    parser.add_argument("--blind-manifest", type=Path, required=True)
    parser.add_argument("--negative-path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_blind_project_reader_experiment(
        args.benchmark_manifest,
        args.blind_manifest,
        negative_path=args.negative_path,
    )
    encoded = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.output is not None:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
