"""A/B experiment for project-scoped protocols plus deterministic proof."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocol, ResourceProtocolMiner
from smartbench.analysis.resource_lifecycle import ProtocolOrigin
from smartbench.benchmarks import load_benchmark_manifest
from smartbench.core import AdapterRegistry, register_all_adapters
from smartbench.engine.project_reader import (
    CandidateSemanticMapping,
    ProjectModel,
    ProjectModelValidator,
    build_project_inventory,
)


@dataclass(frozen=True)
class ProjectReaderExperimentCase:
    case_id: str
    language: str
    protocols: tuple[ResourceProtocol, ...]
    baseline_before_findings: int
    assisted_before_findings: int
    assisted_after_findings: int
    before_abstentions: int
    after_abstentions: int
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "language": self.language,
            "protocols": [
                {
                    "protocol_id": item.protocol_id,
                    "acquire_symbol": item.acquire_symbol,
                    "resource_result_index": item.resource_result_index,
                    "cleanup_methods": list(item.cleanup_methods),
                    "origin": item.origin.value,
                }
                for item in self.protocols
            ],
            "baseline_before_findings": self.baseline_before_findings,
            "assisted_before_findings": self.assisted_before_findings,
            "assisted_after_findings": self.assisted_after_findings,
            "before_abstentions": self.before_abstentions,
            "after_abstentions": self.after_abstentions,
            "passed": self.passed,
        }


@dataclass
class ProjectReaderExperimentReport:
    cases: list[ProjectReaderExperimentCase] = field(default_factory=list)
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

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "smartbench.experiments/project-reader-resource/v1",
            "experiment_type": "reference-assisted-project-model",
            "passed": self.passed,
            "cases": [case.to_dict() for case in self.cases],
            "independent_negatives": {
                "findings": self.independent_negative_findings,
                "abstentions": self.independent_negative_abstentions,
            },
            "errors": list(self.errors),
            "summary": {
                "cases": len(self.cases),
                "baseline_before_detected": sum(
                    case.baseline_before_findings > 0 for case in self.cases
                ),
                "assisted_before_detected": sum(
                    case.assisted_before_findings > 0 for case in self.cases
                ),
                "assisted_after_clean": sum(
                    case.assisted_after_findings == 0 for case in self.cases
                ),
            },
            "limitations": [
                "Protocol hypotheses are learned from fixed reference usage, not a live LLM.",
                "The experiment measures protocol/analyzer separation, not autonomous discovery recall.",
                "Only normalized defer-style cleanup registration is evaluated.",
            ],
        }


def run_project_reader_resource_experiment(
    manifest_path: Path,
    *,
    languages: Iterable[str] = ("go",),
    negative_path: Path | None = None,
) -> ProjectReaderExperimentReport:
    """Learn mappings from fixed references and evaluate buggy snapshots."""
    allowed = {language.lower() for language in languages}
    cases = load_benchmark_manifest(manifest_path.expanduser().resolve())
    registry = AdapterRegistry()
    register_all_adapters(registry)
    miner = ResourceProtocolMiner()
    analyzer = ResourceLifecycleAnalyzer()
    report = ProjectReaderExperimentReport()
    learned_protocols: dict[str, ResourceProtocol] = {}

    for case in cases:
        if case.language not in allowed:
            continue
        adapter = registry.get_adapter_for_language(case.language)
        if adapter is None:
            report.errors.append(f"{case.case_id}: no adapter for {case.language}")
            continue
        snapshots = {snapshot.label: snapshot for snapshot in case.snapshots}
        before = snapshots.get("before")
        after = snapshots.get("after")
        if before is None or after is None:
            report.errors.append(f"{case.case_id}: before/after snapshots are required")
            continue
        before_ir = adapter.parse_semantic_project(before.path)
        after_ir = adapter.parse_semantic_project(after.path)
        learned = tuple(miner.learn(after_ir))
        inventory = build_project_inventory(after_ir, max_facts=1000)
        candidates: list[CandidateSemanticMapping] = []
        for protocol in learned:
            acquire_fact = next(
                (
                    item
                    for item in inventory.facts
                    if item.object == protocol.acquire_symbol
                    and item.attributes.get("primary_result_call") is True
                    and protocol.resource_result_index
                    < len(item.attributes.get("result_targets", ()))
                ),
                None,
            )
            if acquire_fact is None:
                report.errors.append(
                    f"{case.case_id}: no inventory fact for {protocol.acquire_symbol}"
                )
                continue
            result_targets = acquire_fact.attributes.get("result_targets", ())
            binding = str(result_targets[protocol.resource_result_index])
            cleanup_facts = tuple(
                item
                for item in inventory.facts
                if item.subject == acquire_fact.subject
                and item.attributes.get("inventory_role") == "cleanup_registration"
                and _receiver_root(str(item.attributes.get("receiver", ""))) == binding
                and str(item.object).rsplit(".", 1)[-1] in protocol.cleanup_methods
            )
            grounded_methods = {
                str(item.object).rsplit(".", 1)[-1] for item in cleanup_facts
            }
            if set(protocol.cleanup_methods) - grounded_methods:
                report.errors.append(
                    f"{case.case_id}: no cleanup facts for {protocol.acquire_symbol}"
                )
                continue
            candidates.append(
                CandidateSemanticMapping(
                    candidate_id=protocol.protocol_id,
                    operation_id=str(acquire_fact.attributes["operation_id"]),
                    acquire_symbol=protocol.acquire_symbol,
                    resource_result_index=protocol.resource_result_index,
                    cleanup_methods=protocol.cleanup_methods,
                    confidence=protocol.confidence,
                    fact_ids=(
                        acquire_fact.fact_id,
                        *(item.fact_id for item in cleanup_facts),
                    ),
                )
            )
        model = ProjectModel(
            architecture_summary="Reference-derived resource protocol hypotheses.",
            resource_candidates=tuple(candidates),
            uncertainties=("Resource meaning is project-scoped.",),
        )
        validation = ProjectModelValidator().validate(
            after_ir,
            model,
            inventory,
            origin=ProtocolOrigin.REFERENCE_USAGE,
        )
        protocols = validation.protocols
        for decision in validation.decisions:
            if decision.protocol is None:
                report.errors.append(
                    f"{case.case_id}: mapping {decision.candidate_id} rejected: "
                    f"{decision.reason}"
                )
        for protocol in protocols:
            learned_protocols[protocol.protocol_id] = protocol
        baseline = analyzer.analyze(before_ir, ())
        before_result = analyzer.analyze(before_ir, protocols)
        after_result = analyzer.analyze(after_ir, protocols)
        passed = (
            len(before_result.findings) >= before.min_findings
            and (
                before.max_findings is None
                or len(before_result.findings) <= before.max_findings
            )
            and len(after_result.findings) == 0
        )
        report.cases.append(
            ProjectReaderExperimentCase(
                case_id=case.case_id,
                language=case.language,
                protocols=protocols,
                baseline_before_findings=len(baseline.findings),
                assisted_before_findings=len(before_result.findings),
                assisted_after_findings=len(after_result.findings),
                before_abstentions=before_result.abstentions,
                after_abstentions=after_result.abstentions,
                passed=passed,
            )
        )

    if negative_path is not None:
        go_adapter = registry.get_adapter_for_language("go")
        if go_adapter is None:
            report.errors.append("independent negatives: no Go adapter")
        else:
            negative_ir = go_adapter.parse_semantic_project(negative_path.resolve())
            negative_result = analyzer.analyze(
                negative_ir,
                sorted(learned_protocols.values(), key=lambda item: item.protocol_id),
            )
            report.independent_negative_findings = len(negative_result.findings)
            report.independent_negative_abstentions = negative_result.abstentions
    return report


def _receiver_root(receiver: str) -> str:
    receiver = receiver.strip()
    while receiver.startswith(("&", "*", "(")):
        receiver = receiver[1:].lstrip()
    return receiver.split(".", 1)[0].split("(", 1)[0].strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the project-reader resource lifecycle A/B experiment."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--negative-path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_project_reader_resource_experiment(
        args.manifest,
        negative_path=args.negative_path,
    )
    encoded = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
