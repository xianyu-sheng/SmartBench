"""Shared analysis session for deterministic and Agent consumers.

An :class:`AnalysisSession` owns one repository fingerprint, one complete
SemanticIR, and one deterministic rule result.  Interactive review and
ProjectReader stages consume that same immutable semantic boundary instead of
rebuilding a shallow compatibility IR from ``CodeGraph``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from smartbench.analysis.resource_lifecycle import (
    ResourceLifecycleAnalyzer,
    ResourceLifecycleResult,
)
from smartbench.core.adapters import AdapterRegistry, register_all_adapters
from smartbench.core.engine import (
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    UnifiedDiagnosticResult,
)
from smartbench.core.rules import RuleRegistry, register_builtin_rules
from smartbench.core.rules.base import Finding, Location, Severity
from smartbench.engine.project_reader import (
    DeterministicEvidenceResolver,
    EvidenceResolutionStatus,
    MappingDecision,
    MappingStatus,
    ProjectModel,
    ProjectModelResolution,
    ProjectModelValidation,
    ProjectModelValidator,
    ProjectReaderAgent,
    ProjectReaderResult,
)
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.ir import (
    EvidencePack,
    EvidenceRef,
    FactKind,
    SemanticFact,
    SemanticHypothesis,
    SemanticIR,
)


@dataclass
class ProjectReaderStage:
    """Auditable result of the optional project-semantics stage."""

    status: str = "not_run"
    reader_result: Optional[ProjectReaderResult] = None
    resolution: Optional[ProjectModelResolution] = None
    validation: Optional[ProjectModelValidation] = None
    lifecycle: Optional[ResourceLifecycleResult] = None
    repair_attempts: int = 0
    repair_error: str = ""
    findings: list[Finding] = field(default_factory=list)
    facts: tuple[SemanticFact, ...] = ()
    hypotheses: tuple[SemanticHypothesis, ...] = ()

    def to_dict(self) -> dict[str, object]:
        reader_error = self.reader_result.error if self.reader_result else ""
        model = self.reader_result.model if self.reader_result else None
        decisions = self.validation.decisions if self.validation else []
        lifecycle = self.lifecycle
        return {
            "status": self.status,
            "reader_error": reader_error,
            "repair_attempts": self.repair_attempts,
            "repair_error": self.repair_error,
            "proposed_candidates": len(model.resource_candidates) if model else 0,
            "supported_protocols": len(self.validation.protocols) if self.validation else 0,
            "decisions": [
                {
                    "candidate_id": decision.candidate_id,
                    "status": decision.status.value,
                    "reason": decision.reason,
                }
                for decision in decisions
            ],
            "resolution_decisions": [
                decision.to_dict()
                for decision in (self.resolution.decisions if self.resolution else ())
            ],
            "findings": [finding.to_dict() for finding in self.findings],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "abstentions": lifecycle.abstentions if lifecycle else 0,
            "unknown_reasons": list(lifecycle.unknown_reasons) if lifecycle else [],
            "limitations": [
                "ProjectReader output is an untrusted project-scoped hypothesis.",
                "Only uniquely resolved and structurally validated protocols are analyzed.",
                "The current lifecycle proof covers normalized defer-style cleanup.",
            ],
        }


@dataclass
class AnalysisSession:
    """One repository analysis shared by rules, retrieval, and Agents."""

    project_path: Path
    engine: UnifiedDiagnosticEngine
    result: UnifiedDiagnosticResult
    config: UnifiedDiagnosticConfig
    project_reader: ProjectReaderStage = field(default_factory=ProjectReaderStage)

    @classmethod
    def analyze(
        cls,
        project_path: Path | str,
        config: Optional[UnifiedDiagnosticConfig] = None,
        *,
        engine: Optional[UnifiedDiagnosticEngine] = None,
    ) -> "AnalysisSession":
        """Build the complete SemanticIR and run deterministic rules once."""
        root = Path(project_path).expanduser().resolve()
        active_config = config or UnifiedDiagnosticConfig()
        active_engine = engine or _default_engine()
        result = active_engine.diagnose(root, active_config)
        result.pipeline = {
            "session": "analysis-session/v1",
            "shared_semantic_ir": result.ir is not None,
            "deterministic_rules": True,
            "agent_review": "optional",
        }
        result.project_reader = ProjectReaderStage().to_dict()
        return cls(
            project_path=root,
            engine=active_engine,
            result=result,
            config=active_config,
        )

    @property
    def ir(self) -> Optional[SemanticIR]:
        return self.result.ir

    @property
    def fingerprint(self):
        return self.result.fingerprint

    def run_project_reader(
        self,
        llm_call_fn: Callable[..., str],
        *,
        max_inventory_facts: int = 300,
        max_repairs: int = 1,
    ) -> ProjectReaderStage:
        """Run hypothesis, deterministic resolution, validation, and analysis.

        A repair is permitted only when every initial candidate was rejected.
        The repair receives the same inventory and deterministic rejection
        reasons; it never receives an analyzer answer.
        """
        ir = self.ir
        if ir is None or not ir.operations:
            self.project_reader = ProjectReaderStage(status="unsupported")
            self.result.project_reader = self.project_reader.to_dict()
            return self.project_reader

        reader = ProjectReaderAgent(
            llm_call_fn,
            max_inventory_facts=max_inventory_facts,
        )
        resolver = DeterministicEvidenceResolver()
        validator = ProjectModelValidator()
        reader_result = reader.read(ir)
        stage = ProjectReaderStage(reader_result=reader_result)
        if reader_result.model is None:
            stage.status = "unavailable" if reader_result.error else "abstained"
            self.project_reader = stage
            self.result.project_reader = stage.to_dict()
            return stage

        current_model = reader_result.model
        resolution, validation = _resolve_and_validate(
            resolver,
            validator,
            ir,
            current_model,
            reader_result.inventory,
        )
        repair_limit = max(0, min(int(max_repairs), 3))
        while (
            not validation.protocols
            and _has_rejections(validation)
            and stage.repair_attempts < repair_limit
        ):
            repaired = reader.repair(
                reader_result.inventory,
                current_model,
                validation,
            )
            stage.repair_attempts += 1
            if repaired.model is None:
                stage.repair_error = repaired.error
                break
            current_model = repaired.model
            reader_result = ProjectReaderResult(
                inventory=reader_result.inventory,
                model=current_model,
            )
            resolution, validation = _resolve_and_validate(
                resolver,
                validator,
                ir,
                current_model,
                reader_result.inventory,
            )

        lifecycle = ResourceLifecycleAnalyzer().analyze(ir, validation.protocols)
        facts = tuple(finding.to_fact() for finding in lifecycle.findings)
        findings = [_resource_finding(self, finding) for finding in lifecycle.findings]
        stage.reader_result = reader_result
        stage.resolution = resolution
        stage.validation = validation
        stage.lifecycle = lifecycle
        stage.findings = findings
        stage.facts = facts
        stage.hypotheses = _project_reader_hypotheses(reader_result, validation)
        if stage.repair_error:
            stage.status = "abstained"
        elif findings:
            stage.status = "findings"
        elif validation.protocols:
            stage.status = "supported_no_finding"
        else:
            stage.status = "abstained"

        self.project_reader = stage
        self.result.project_reader = stage.to_dict()
        self._merge_project_reader_findings(stage)
        return stage

    def build_evidence_pack(
        self,
        query: str,
        *,
        hops: int = 2,
        max_nodes: int = 16,
        max_diagnostic_facts: int = 12,
    ) -> EvidencePack:
        """Retrieve one bounded pack from the session's complete SemanticIR."""
        if self.ir is None:
            return EvidencePack(query=query, retrieval_trace=("session:no-ir",))
        base = DeterministicGraphRAG(self.ir).retrieve(
            query,
            hops=hops,
            max_nodes=max_nodes,
        )
        diagnostic_facts = [
            fact
            for finding in self.result.findings
            if (fact := _diagnostic_finding_fact(self.ir, finding)) is not None
        ][: max(0, int(max_diagnostic_facts))]
        diagnostic_hypotheses = [
            _diagnostic_finding_hypothesis(finding)
            for finding in self.result.findings
            if str(finding.metadata.get("analysis_method", "unknown")) != "semantic"
        ][: max(0, int(max_diagnostic_facts))]
        combined = [
            *self.project_reader.facts,
            *(fact for fact in diagnostic_facts if fact is not None),
            *base.facts,
        ]
        unique: list[SemanticFact] = []
        seen: set[str] = set()
        for fact in combined:
            if fact.fact_id in seen:
                continue
            seen.add(fact.fact_id)
            unique.append(fact)
        version_material = "|".join(
            (
                base.graph_version,
                *(fact.fact_id for fact in unique),
                *(item.hypothesis_id for item in self.project_reader.hypotheses),
                *(item.hypothesis_id for item in diagnostic_hypotheses),
            )
        )
        return EvidencePack.from_facts(
            query,
            unique,
            hypotheses=(
                *self.project_reader.hypotheses,
                *diagnostic_hypotheses,
            ),
            retrieval_trace=(
                "analysis-session:semantic-ir",
                f"deterministic-findings:{len(diagnostic_facts)}",
                f"project-reader-facts:{len(self.project_reader.facts)}",
                f"untrusted-hypotheses:{len(self.project_reader.hypotheses) + len(diagnostic_hypotheses)}",
                *base.retrieval_trace,
            ),
            graph_version=(
                "session-"
                + hashlib.sha256(version_material.encode("utf-8")).hexdigest()[:16]
            ),
        )

    def report_dict(self) -> dict[str, object]:
        """Return a JSON-safe report shared by CLI consumers."""
        self.result.project_reader = self.project_reader.to_dict()
        return self.result.to_dict()

    def _merge_project_reader_findings(self, stage: ProjectReaderStage) -> None:
        if not stage.findings:
            return
        known = {
            (
                finding.rule_id,
                finding.location.file_path,
                finding.location.line_start,
                finding.message,
            )
            for finding in self.result.findings
        }
        for finding in stage.findings:
            key = (
                finding.rule_id,
                finding.location.file_path,
                finding.location.line_start,
                finding.message,
            )
            if key not in known:
                self.result.findings.append(finding)
                known.add(key)
        self.result.analysis_status["project_resource_lifecycle"] = {
            "status": "partial",
            "analysis_method": "semantic",
            "reason": (
                "project-scoped protocol accepted by deterministic evidence gates; "
                "current proof covers normalized defer-style cleanup only"
            ),
        }
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        findings = self.result.findings
        self.result.stats["findings_total"] = len(findings)
        self.result.stats["findings_error"] = sum(
            finding.severity == Severity.ERROR for finding in findings
        )
        self.result.stats["findings_warning"] = sum(
            finding.severity == Severity.WARNING for finding in findings
        )
        self.result.stats["findings_info"] = sum(
            finding.severity == Severity.INFO for finding in findings
        )
        statuses = [
            entry.get("status")
            for entry in self.result.analysis_status.values()
        ]
        self.result.stats["rules_total"] = len(statuses)
        for status in ("full", "partial", "unknown", "unsupported"):
            self.result.stats[f"rules_{status}"] = sum(
                value == status for value in statuses
            )


def _default_engine() -> UnifiedDiagnosticEngine:
    adapters = AdapterRegistry()
    register_all_adapters(adapters)
    rules = RuleRegistry()
    register_builtin_rules(rules)
    return UnifiedDiagnosticEngine(adapters, rules)


def _resolve_and_validate(
    resolver: DeterministicEvidenceResolver,
    validator: ProjectModelValidator,
    ir: SemanticIR,
    model: ProjectModel,
    inventory: EvidencePack,
) -> tuple[ProjectModelResolution, ProjectModelValidation]:
    resolution = resolver.resolve(ir, model, inventory)
    validation = validator.validate(ir, resolution.model, inventory)
    for decision in resolution.decisions:
        if decision.status == EvidenceResolutionStatus.RESOLVED:
            continue
        validation.decisions.append(
            MappingDecision(
                candidate_id=decision.candidate_id,
                status=MappingStatus.REJECTED,
                reason=(
                    f"evidence resolution {decision.status.value}: "
                    f"{decision.reason}"
                ),
            )
        )
    return resolution, validation


def _has_rejections(validation: ProjectModelValidation) -> bool:
    return any(
        decision.status == MappingStatus.REJECTED
        for decision in validation.decisions
    )


def _resource_finding(session: AnalysisSession, finding: Any) -> Finding:
    fact = finding.to_fact()
    acquire = finding.acquire.location
    use = finding.first_unprotected_use.location
    unit = session.ir.source_units.get(acquire.file_path) if session.ir else None
    metadata: dict[str, Any] = dict(fact.attributes)
    metadata.update(
        {
            "analysis_method": "semantic",
            "analysis_status": "partial",
            "project_reader_grounded": True,
        }
    )
    if unit is not None:
        metadata["source_role"] = unit.role.value
        metadata["repository_zone"] = unit.repository_zone.value
    evidence = [
        Location(
            file_path=ref.file_path,
            line_start=ref.line_start,
            line_end=ref.line_end,
            column_start=ref.column_start,
            column_end=ref.column_end,
        )
        for ref in fact.evidence
    ]
    return Finding(
        rule_id="project_resource_lifecycle",
        rule_name="Project-derived resource lifecycle",
        severity=Severity.WARNING,
        location=Location(
            file_path=use.file_path,
            line_start=use.line_start,
            line_end=use.line_end,
            column_start=use.column_start,
            column_end=use.column_end,
        ),
        message=(
            f"Resource acquired by {finding.acquire.target} is used without a "
            "dominating validated cleanup registration"
        ),
        evidence=evidence,
        confidence=finding.protocol.confidence,
        metadata=metadata,
    )


def _diagnostic_finding_fact(
    ir: SemanticIR,
    finding: Finding,
) -> Optional[SemanticFact]:
    location = finding.location
    if not location.file_path or location.line_start < 1:
        return None
    unit = ir.source_units.get(location.file_path)
    method = str(finding.metadata.get("analysis_method", "unknown"))
    if method != "semantic":
        return None
    status = str(finding.metadata.get("analysis_status", "unknown"))
    return SemanticFact(
        subject=finding.rule_id,
        predicate=FactKind.STATE_TRANSITION,
        object=finding.message,
        evidence=(
            EvidenceRef(
                file_path=location.file_path,
                line_start=location.line_start,
                line_end=location.line_end,
                column_start=location.column_start,
                column_end=location.column_end,
                source=f"diagnostic_rule:{finding.rule_id}",
            ),
        ),
        confidence=finding.confidence,
        attributes={
            "kind": "deterministic_diagnostic_candidate",
            "rule_id": finding.rule_id,
            "analysis_method": method,
            "analysis_status": status,
            "source_role": unit.role.value if unit else "unknown",
            "repository_zone": unit.repository_zone.value if unit else "unknown",
            "claim_boundary": "semantic analyzer output",
        },
    )


def _diagnostic_finding_hypothesis(finding: Finding) -> SemanticHypothesis:
    return SemanticHypothesis(
        kind="heuristic_diagnostic_candidate",
        statement=finding.message,
        source=f"diagnostic_rule:{finding.rule_id}",
        confidence=finding.confidence,
        attributes={
            "rule_id": finding.rule_id,
            "file_path": finding.location.file_path,
            "line_start": finding.location.line_start,
            "analysis_status": finding.metadata.get("analysis_status", "unknown"),
            "claim_boundary": "untrusted candidate; must be supported by fact IDs",
        },
    )


def _project_reader_hypotheses(
    reader_result: ProjectReaderResult,
    validation: ProjectModelValidation,
) -> tuple[SemanticHypothesis, ...]:
    model = reader_result.model
    if model is None:
        return ()
    decision_by_id = {
        decision.candidate_id: decision
        for decision in validation.decisions
    }
    hypotheses: list[SemanticHypothesis] = []
    if model.architecture_summary.strip():
        hypotheses.append(
            SemanticHypothesis(
                kind="project_architecture_summary",
                statement=model.architecture_summary,
                source="project_reader",
                attributes={
                    "components": list(model.components),
                    "uncertainties": list(model.uncertainties),
                },
            )
        )
    for candidate in model.resource_candidates:
        decision = decision_by_id.get(candidate.candidate_id)
        hypotheses.append(
            SemanticHypothesis(
                kind="project_resource_protocol",
                statement=(
                    f"{candidate.acquire_symbol} result {candidate.resource_result_index} "
                    f"may require {', '.join(candidate.cleanup_methods)}"
                ),
                source="project_reader",
                confidence=candidate.confidence,
                attributes={
                    "candidate_id": candidate.candidate_id,
                    "validation_status": (
                        decision.status.value if decision else "unknown"
                    ),
                    "validation_reason": decision.reason if decision else "",
                    "operation_id": candidate.operation_id,
                },
            )
        )
    return tuple(hypotheses)
