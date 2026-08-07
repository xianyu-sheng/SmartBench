"""Online ProjectReader experiment over source-backed reference inventories.

Raw prompts and model responses are deliberately not included in the report.
Only parsed candidate counts, deterministic validation decisions, and analyzer
outcomes are persisted.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocolMiner
from smartbench.benchmarks import load_benchmark_manifest
from smartbench.core import AdapterRegistry, register_all_adapters
from smartbench.engine.project_reader import (
    MappingStatus,
    ProjectModelValidator,
    ProjectReaderAgent,
)
from smartbench.llm.client import call_llm
from smartbench.llm.provider import load_api_keys_from_env


@dataclass(frozen=True)
class OnlineModelDescriptor:
    """Non-secret provider metadata safe to persist in an experiment report."""

    provider: str
    model: str

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


@dataclass(frozen=True)
class OnlineMappingDecision:
    candidate_id: str
    status: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OnlineProjectReaderCase:
    case_id: str
    language: str
    reader_error: str
    proposed_candidates: int
    supported_protocols: int
    rejected_candidates: int
    reference_protocols: int
    reference_protocol_matches: int
    before_findings: int
    after_findings: int
    before_abstentions: int
    after_abstentions: int
    decisions: tuple[OnlineMappingDecision, ...]
    passed: bool

    @property
    def reference_protocol_recall(self) -> float:
        if not self.reference_protocols:
            return 1.0
        return self.reference_protocol_matches / self.reference_protocols

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "language": self.language,
            "reader_error": self.reader_error,
            "proposed_candidates": self.proposed_candidates,
            "supported_protocols": self.supported_protocols,
            "rejected_candidates": self.rejected_candidates,
            "reference_protocols": self.reference_protocols,
            "reference_protocol_matches": self.reference_protocol_matches,
            "reference_protocol_recall": round(self.reference_protocol_recall, 4),
            "before_findings": self.before_findings,
            "after_findings": self.after_findings,
            "before_abstentions": self.before_abstentions,
            "after_abstentions": self.after_abstentions,
            "decisions": [item.to_dict() for item in self.decisions],
            "passed": self.passed,
        }


@dataclass
class OnlineProjectReaderReport:
    status: str = "completed"
    models: tuple[OnlineModelDescriptor, ...] = ()
    cases: list[OnlineProjectReaderCase] = field(default_factory=list)
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
        return {
            "schema_version": "smartbench.experiments/project-reader-online/v1",
            "experiment_type": "online-project-reader-reference-inventory",
            "status": self.status,
            "passed": self.passed,
            "models": [model.to_dict() for model in self.models],
            "cases": [case.to_dict() for case in self.cases],
            "independent_negatives": {
                "findings": self.independent_negative_findings,
                "abstentions": self.independent_negative_abstentions,
            },
            "errors": list(self.errors),
            "summary": {
                "cases": len(self.cases),
                "passed_cases": sum(case.passed for case in self.cases),
                "proposed_candidates": sum(
                    case.proposed_candidates for case in self.cases
                ),
                "supported_protocols": sum(
                    case.supported_protocols for case in self.cases
                ),
                "rejected_candidates": sum(
                    case.rejected_candidates for case in self.cases
                ),
                "reference_protocols": sum(
                    case.reference_protocols for case in self.cases
                ),
                "reference_protocol_matches": sum(
                    case.reference_protocol_matches for case in self.cases
                ),
                "before_detected": sum(
                    case.before_findings > 0 for case in self.cases
                ),
                "after_clean": sum(case.after_findings == 0 for case in self.cases),
            },
            "privacy": {
                "raw_prompts_persisted": False,
                "raw_model_responses_persisted": False,
                "api_keys_persisted": False,
            },
            "limitations": [
                "The model reads fixed reference inventories, not buggy snapshots.",
                "This measures online protocol extraction, not unknown-bug discovery recall.",
                "Only candidates accepted by the deterministic citation gate are analyzed.",
                "Only normalized defer-style cleanup registration is evaluated.",
            ],
        }


def sanitized_model_descriptors(api_config: Mapping[str, object]) -> tuple[OnlineModelDescriptor, ...]:
    """Extract only provider and model names from an in-memory configuration."""
    raw_models = api_config.get("models", ())
    if not isinstance(raw_models, list):
        return ()
    descriptors: list[OnlineModelDescriptor] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        provider = item.get("provider", "")
        model = item.get("model", "")
        if isinstance(provider, str) and isinstance(model, str) and provider and model:
            descriptors.append(OnlineModelDescriptor(provider=provider, model=model))
    return tuple(descriptors)


def run_online_project_reader_experiment(
    manifest_path: Path,
    llm_call_fn: Callable[..., str],
    *,
    models: tuple[OnlineModelDescriptor, ...] = (),
    languages: Iterable[str] = ("go",),
    negative_path: Path | None = None,
    max_inventory_facts: int = 1000,
) -> OnlineProjectReaderReport:
    """Ask a live model for protocols, then evaluate only validated mappings."""
    allowed = {language.lower() for language in languages}
    cases = load_benchmark_manifest(manifest_path.expanduser().resolve())
    registry = AdapterRegistry()
    register_all_adapters(registry)
    analyzer = ResourceLifecycleAnalyzer()
    miner = ResourceProtocolMiner()
    validator = ProjectModelValidator()
    reader = ProjectReaderAgent(
        llm_call_fn,
        max_inventory_facts=max_inventory_facts,
    )
    report = OnlineProjectReaderReport(models=models)
    accepted_protocols = {}

    for case in cases:
        if case.language not in allowed:
            continue
        # State-rule-only fixtures (configuration validation, concurrency
        # patterns that the resource-protocol pipeline cannot express) are
        # validated by the benchmark runner, not by this online experiment.
        if str(case.metadata.get("experiment_scope", "resource-protocol")) != "resource-protocol":
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
        reference_protocols = tuple(miner.learn(after_ir))
        reader_result = reader.read(after_ir)
        proposed_candidates = (
            len(reader_result.model.resource_candidates)
            if reader_result.model is not None
            else 0
        )
        if reader_result.model is None:
            protocols = ()
            decisions: tuple[OnlineMappingDecision, ...] = ()
        else:
            validation = validator.validate(
                after_ir,
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
        for protocol in protocols:
            accepted_protocols[protocol.protocol_id] = protocol

        reference_keys = {_protocol_key(protocol) for protocol in reference_protocols}
        supported_keys = {_protocol_key(protocol) for protocol in protocols}
        reference_matches = len(reference_keys & supported_keys)
        before_result = analyzer.analyze(before_ir, protocols)
        after_result = analyzer.analyze(after_ir, protocols)
        before_count = len(before_result.findings)
        after_count = len(after_result.findings)
        passed = (
            not reader_result.error
            and before_count >= before.min_findings
            and (before.max_findings is None or before_count <= before.max_findings)
            and after_count >= after.min_findings
            and (after.max_findings is None or after_count <= after.max_findings)
        )
        report.cases.append(
            OnlineProjectReaderCase(
                case_id=case.case_id,
                language=case.language,
                reader_error=reader_result.error,
                proposed_candidates=proposed_candidates,
                supported_protocols=len(protocols),
                rejected_candidates=sum(
                    decision.status == MappingStatus.REJECTED
                    for decision in (
                        validation.decisions if reader_result.model is not None else ()
                    )
                ),
                reference_protocols=len(reference_keys),
                reference_protocol_matches=reference_matches,
                before_findings=before_count,
                after_findings=after_count,
                before_abstentions=before_result.abstentions,
                after_abstentions=after_result.abstentions,
                decisions=decisions,
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
                sorted(accepted_protocols.values(), key=lambda item: item.protocol_id),
            )
            report.independent_negative_findings = len(negative_result.findings)
            report.independent_negative_abstentions = negative_result.abstentions
    return report


def unavailable_online_report(reason: str) -> OnlineProjectReaderReport:
    return OnlineProjectReaderReport(status="unavailable", errors=[reason])


def _protocol_key(protocol: object) -> tuple[object, ...]:
    return (
        getattr(protocol, "acquire_symbol"),
        getattr(protocol, "resource_result_index"),
        getattr(protocol, "acquire_match_mode"),
        getattr(protocol, "resource_member_path"),
        tuple(sorted(getattr(protocol, "cleanup_methods"))),
    )


def _write_report(report: OnlineProjectReaderReport, output: Path | None) -> None:
    encoded = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if output is not None:
        destination = output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the live ProjectReader protocol extraction experiment."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--negative-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-inventory-facts", type=int, default=1000)
    args = parser.parse_args()

    api_config = load_api_keys_from_env()
    if not api_config:
        report = unavailable_online_report(
            "No supported LLM provider environment variable is configured."
        )
        _write_report(report, args.output)
        return 2

    def invoke(prompt: str, role: str = "project_reader") -> str:
        return call_llm(
            api_config,
            prompt,
            role=role,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
        )

    report = run_online_project_reader_experiment(
        args.manifest,
        invoke,
        models=sanitized_model_descriptors(api_config),
        negative_path=args.negative_path,
        max_inventory_facts=args.max_inventory_facts,
    )
    _write_report(report, args.output)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
