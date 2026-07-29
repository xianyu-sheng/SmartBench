"""Live ProjectReader trials over target-excluded reference inventories.

The model never receives the historical target snapshots in this experiment.
It proposes protocols from pinned blind references; deterministic validation
and analysis are applied to before/after snapshots only after the model call.
Raw prompts and responses are never persisted in the report.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocolMiner
from smartbench.benchmarks import load_benchmark_manifest
from smartbench.core import AdapterRegistry, register_all_adapters
from smartbench.engine.project_reader import (
    MappingStatus,
    ProjectModelValidator,
    ProjectReaderAgent,
)
from smartbench.experiments.project_reader_blind import (
    BlindCaseSpec,
    _finding_witness,
    _protocol_dict,
    _source_hashes_verified,
    _target_paths_excluded,
    load_blind_manifest,
)
from smartbench.experiments.project_reader_online import (
    OnlineMappingDecision,
    OnlineModelDescriptor,
    sanitized_model_descriptors,
)
from smartbench.llm.client import call_llm
from smartbench.llm.provider import load_api_keys_from_env

ONLINE_BLIND_SCHEMA_VERSION = "smartbench.experiments/project-reader-blind-online/v1"


@dataclass(frozen=True)
class OnlineBlindTrial:
    trial: int
    reader_error: str
    repair_error: str
    repair_attempts: int
    recovered_by_repair: bool
    proposed_candidates: int
    initial_rejected_candidates: int
    supported_protocols: int
    rejected_candidates: int
    reference_protocols: int
    reference_protocol_matches: int
    before_findings: int
    after_findings: int
    before_abstentions: int
    after_abstentions: int
    negative_findings: int
    negative_abstentions: int
    protocols: tuple[dict[str, object], ...]
    finding_witnesses: tuple[dict[str, object], ...]
    decisions: tuple[OnlineMappingDecision, ...]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "trial": self.trial,
            "reader_error": self.reader_error,
            "repair_error": self.repair_error,
            "repair_attempts": self.repair_attempts,
            "recovered_by_repair": self.recovered_by_repair,
            "proposed_candidates": self.proposed_candidates,
            "initial_rejected_candidates": self.initial_rejected_candidates,
            "supported_protocols": self.supported_protocols,
            "rejected_candidates": self.rejected_candidates,
            "reference_protocols": self.reference_protocols,
            "reference_protocol_matches": self.reference_protocol_matches,
            "before_findings": self.before_findings,
            "after_findings": self.after_findings,
            "before_abstentions": self.before_abstentions,
            "after_abstentions": self.after_abstentions,
            "negative_findings": self.negative_findings,
            "negative_abstentions": self.negative_abstentions,
            "protocols": list(self.protocols),
            "finding_witnesses": list(self.finding_witnesses),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "passed": self.passed,
        }


@dataclass(frozen=True)
class OnlineBlindCase:
    case_id: str
    source_repository: str
    reference_available: bool
    unsupported_reason: str
    target_paths_excluded: bool
    source_hashes_verified: bool
    expected_before_findings: int
    trials: tuple[OnlineBlindTrial, ...]
    passed: bool

    @property
    def stable_detected(self) -> bool:
        return bool(self.trials) and all(
            trial.before_findings > 0 and trial.passed for trial in self.trials
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "source_repository": self.source_repository,
            "reference_available": self.reference_available,
            "unsupported_reason": self.unsupported_reason,
            "target_paths_excluded": self.target_paths_excluded,
            "source_hashes_verified": self.source_hashes_verified,
            "expected_before_findings": self.expected_before_findings,
            "stable_detected": self.stable_detected,
            "trials": [trial.to_dict() for trial in self.trials],
            "passed": self.passed,
        }


@dataclass
class OnlineBlindReport:
    status: str = "completed"
    models: tuple[OnlineModelDescriptor, ...] = ()
    trials_requested: int = 1
    max_repairs: int = 0
    cases: list[OnlineBlindCase] = field(default_factory=list)
    independent_negative_findings: int = 0
    independent_negative_abstentions: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.status == "completed"
            and bool(self.cases)
            and all(case.passed for case in self.cases)
            and self.independent_negative_findings == 0
            and not self.errors
        )

    def to_dict(self) -> dict[str, object]:
        case_count = len(self.cases)
        stable_detected = sum(case.stable_detected for case in self.cases)
        coverage = stable_detected / case_count if case_count else 0.0
        all_trials = [trial for case in self.cases for trial in case.trials]
        return {
            "schema_version": ONLINE_BLIND_SCHEMA_VERSION,
            "experiment_type": "online-blind-project-reader-protocol-transfer",
            "status": self.status,
            "passed": self.passed,
            "decision": (
                "partial"
                if 0.0 < coverage < 1.0
                else "supported"
                if coverage == 1.0
                else "unsupported"
            ),
            "models": [model.to_dict() for model in self.models],
            "trials_requested": self.trials_requested,
            "max_repairs": self.max_repairs,
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
                "trials_executed": len(all_trials),
                "trials_passed": sum(trial.passed for trial in all_trials),
                "proposed_candidates": sum(
                    trial.proposed_candidates for trial in all_trials
                ),
                "supported_protocols": sum(
                    trial.supported_protocols for trial in all_trials
                ),
                "rejected_candidates": sum(
                    trial.rejected_candidates for trial in all_trials
                ),
                "initial_rejected_candidates": sum(
                    trial.initial_rejected_candidates for trial in all_trials
                ),
                "initially_accepted_trials": sum(
                    trial.repair_attempts == 0 and trial.supported_protocols > 0
                    for trial in all_trials
                ),
                "repair_attempts": sum(
                    trial.repair_attempts for trial in all_trials
                ),
                "recovered_trials": sum(
                    trial.recovered_by_repair for trial in all_trials
                ),
                "stable_detected_cases": stable_detected,
                "stable_diagnostic_coverage": round(coverage, 4),
            },
            "privacy": {
                "target_snapshots_in_model_prompt": False,
                "raw_prompts_persisted": False,
                "raw_model_responses_persisted": False,
                "api_keys_persisted": False,
            },
            "limitations": [
                "The model sees pinned safe reference inventories, never historical target snapshots.",
                "Cases without an admissible target-excluded reference remain unsupported.",
                "This measures blind protocol extraction and transfer, not arbitrary unknown-bug recall.",
                "Only deterministically validated candidates reach before/after analysis.",
                f"Each trial permits at most {self.max_repairs} evidence-feedback repair attempts.",
                "No finding authorizes an upstream issue or pull request.",
            ],
        }


def run_online_blind_project_reader_experiment(
    benchmark_manifest: Path,
    blind_manifest: Path,
    llm_call_fn: Callable[..., str],
    *,
    models: tuple[OnlineModelDescriptor, ...] = (),
    negative_path: Path | None = None,
    trials: int = 3,
    max_repairs: int = 1,
    max_inventory_facts: int = 1000,
) -> OnlineBlindReport:
    """Run repeated live-model trials without exposing target code to the model."""
    trial_count = max(1, min(int(trials), 10))
    repair_limit = max(0, min(int(max_repairs), 3))
    benchmark_cases = {
        case.case_id: case
        for case in load_benchmark_manifest(benchmark_manifest.expanduser().resolve())
    }
    specs = load_blind_manifest(blind_manifest)
    registry = AdapterRegistry()
    register_all_adapters(registry)
    analyzer = ResourceLifecycleAnalyzer()
    miner = ResourceProtocolMiner()
    validator = ProjectModelValidator()
    reader = ProjectReaderAgent(
        llm_call_fn,
        max_inventory_facts=max_inventory_facts,
    )
    report = OnlineBlindReport(
        models=models,
        trials_requested=trial_count,
        max_repairs=repair_limit,
    )
    negative_ir = None
    if negative_path is not None:
        adapter = registry.get_adapter_for_language("go")
        if adapter is None:
            report.errors.append("independent negatives: no Go adapter")
        else:
            negative_ir = adapter.parse_semantic_project(negative_path.resolve())

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

        excluded = _target_paths_excluded(spec)
        hashes_verified = _source_hashes_verified(spec)
        case_trials: list[OnlineBlindTrial] = []
        if spec.reference_path is not None:
            reference_ir = adapter.parse_semantic_project(spec.reference_path)
            before_ir = adapter.parse_semantic_project(before.path)
            after_ir = adapter.parse_semantic_project(after.path)
            reference_protocols = tuple(
                miner.learn(reference_ir, generalize_method_shapes=True)
            )
            reference_keys = {_protocol_key(protocol) for protocol in reference_protocols}
            for trial_number in range(1, trial_count + 1):
                trial = _run_trial(
                    trial_number,
                    reader,
                    validator,
                    analyzer,
                    reference_ir,
                    before_ir,
                    after_ir,
                    negative_ir,
                    reference_keys,
                    spec,
                    repair_limit,
                )
                case_trials.append(trial)

        case_passed = (
            excluded
            and hashes_verified
            and (
                all(trial.passed for trial in case_trials)
                if spec.reference_path is not None
                else bool(spec.unsupported_reason)
                and spec.expected_shape_before_findings == 0
            )
        )
        report.cases.append(
            OnlineBlindCase(
                case_id=spec.benchmark_case_id,
                source_repository=spec.source_repository,
                reference_available=spec.reference_path is not None,
                unsupported_reason=spec.unsupported_reason,
                target_paths_excluded=excluded,
                source_hashes_verified=hashes_verified,
                expected_before_findings=spec.expected_shape_before_findings,
                trials=tuple(case_trials),
                passed=case_passed,
            )
        )

    # Every accepted trial already runs the independent negative. Keep the
    # aggregate fields conservative without reconstructing discarded model output.
    negative_trials = [
        trial
        for case in report.cases
        for trial in case.trials
        if trial.negative_findings > 0
    ]
    report.independent_negative_findings = sum(
        trial.negative_findings for trial in negative_trials
    )
    report.independent_negative_abstentions = sum(
        trial.negative_abstentions
        for case in report.cases
        for trial in case.trials
    )
    return report


def _run_trial(
    trial_number: int,
    reader: ProjectReaderAgent,
    validator: ProjectModelValidator,
    analyzer: ResourceLifecycleAnalyzer,
    reference_ir,
    before_ir,
    after_ir,
    negative_ir,
    reference_keys: set[tuple[object, ...]],
    spec: BlindCaseSpec,
    max_repairs: int,
) -> OnlineBlindTrial:
    reader_result = reader.read(reference_ir)
    initial_proposed = (
        len(reader_result.model.resource_candidates)
        if reader_result.model is not None
        else 0
    )
    protocols = ()
    decisions: tuple[OnlineMappingDecision, ...] = ()
    validation = None
    if reader_result.model is not None:
        validation = validator.validate(
            reference_ir,
            reader_result.model,
            reader_result.inventory,
        )
        protocols = validation.protocols
        decisions = tuple(
            OnlineMappingDecision(
                candidate_id=decision.candidate_id,
                status=decision.status.value,
                reason=decision.reason,
            )
            for decision in validation.decisions
        )
    initial_rejected = sum(
        decision.status == MappingStatus.REJECTED.value for decision in decisions
    )
    repair_attempts = 0
    repair_error = ""
    current_model = reader_result.model
    while (
        current_model is not None
        and validation is not None
        and not protocols
        and initial_rejected
        and repair_attempts < max_repairs
    ):
        repaired = reader.repair(reader_result.inventory, current_model, validation)
        repair_attempts += 1
        if repaired.model is None:
            repair_error = repaired.error
            break
        current_model = repaired.model
        validation = validator.validate(
            reference_ir,
            current_model,
            reader_result.inventory,
        )
        protocols = validation.protocols
        decisions = tuple(
            OnlineMappingDecision(
                candidate_id=decision.candidate_id,
                status=decision.status.value,
                reason=decision.reason,
            )
            for decision in validation.decisions
        )
    proposed = (
        len(current_model.resource_candidates)
        if current_model is not None
        else initial_proposed
    )
    supported_keys = {_protocol_key(protocol) for protocol in protocols}
    before_result = analyzer.analyze(before_ir, protocols)
    after_result = analyzer.analyze(after_ir, protocols)
    negative_result = (
        analyzer.analyze(negative_ir, protocols) if negative_ir is not None else None
    )
    negative_findings = len(negative_result.findings) if negative_result else 0
    negative_abstentions = negative_result.abstentions if negative_result else 0
    before_count = len(before_result.findings)
    after_count = len(after_result.findings)
    passed = (
        not reader_result.error
        and before_count == spec.expected_shape_before_findings
        and after_count == 0
        and negative_findings == 0
    )
    return OnlineBlindTrial(
        trial=trial_number,
        reader_error=reader_result.error,
        repair_error=repair_error,
        repair_attempts=repair_attempts,
        recovered_by_repair=bool(repair_attempts and not repair_error and protocols),
        proposed_candidates=proposed,
        initial_rejected_candidates=initial_rejected,
        supported_protocols=len(protocols),
        rejected_candidates=sum(
            decision.status == MappingStatus.REJECTED.value for decision in decisions
        ),
        reference_protocols=len(reference_keys),
        reference_protocol_matches=len(reference_keys & supported_keys),
        before_findings=before_count,
        after_findings=after_count,
        before_abstentions=before_result.abstentions,
        after_abstentions=after_result.abstentions,
        negative_findings=negative_findings,
        negative_abstentions=negative_abstentions,
        protocols=tuple(_protocol_dict(protocol) for protocol in protocols),
        finding_witnesses=tuple(
            _finding_witness(finding) for finding in before_result.findings
        ),
        decisions=decisions,
        passed=passed,
    )


def _protocol_key(protocol: object) -> tuple[object, ...]:
    return (
        getattr(protocol, "acquire_symbol"),
        getattr(protocol, "resource_result_index"),
        getattr(protocol, "acquire_match_mode"),
        getattr(protocol, "resource_member_path"),
        getattr(protocol, "receiver_type"),
        getattr(protocol, "canonical_acquire"),
        tuple(sorted(getattr(protocol, "cleanup_methods"))),
    )


def unavailable_online_blind_report(
    reason: str,
    trials: int,
    max_repairs: int = 1,
) -> OnlineBlindReport:
    return OnlineBlindReport(
        status="unavailable",
        trials_requested=max(1, min(int(trials), 10)),
        max_repairs=max(0, min(int(max_repairs), 3)),
        errors=[reason],
    )


def write_online_blind_report(report: OnlineBlindReport, output: Path | None) -> None:
    encoded = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if output is not None:
        destination = output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repeated live ProjectReader trials over blind references."
    )
    parser.add_argument("--benchmark-manifest", type=Path, required=True)
    parser.add_argument("--blind-manifest", type=Path, required=True)
    parser.add_argument("--negative-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-repairs", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-inventory-facts", type=int, default=1000)
    args = parser.parse_args()

    api_config = load_api_keys_from_env()
    if not api_config:
        report = unavailable_online_blind_report(
            "No supported LLM provider environment variable is configured.",
            args.trials,
            args.max_repairs,
        )
        write_online_blind_report(report, args.output)
        return 2

    def invoke(prompt: str, role: str = "project_reader") -> str:
        return call_llm(
            api_config,
            prompt,
            role=role,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
        )

    report = run_online_blind_project_reader_experiment(
        args.benchmark_manifest,
        args.blind_manifest,
        invoke,
        models=sanitized_model_descriptors(api_config),
        negative_path=args.negative_path,
        trials=args.trials,
        max_repairs=args.max_repairs,
        max_inventory_facts=args.max_inventory_facts,
    )
    write_online_blind_report(report, args.output)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
