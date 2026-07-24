"""
Unified Diagnostic Engine - Main orchestrator.

This engine brings together:
  - Adapters: Parse code into IR
  - Rules: Detect issues from IR
  - Registry: Manage available adapters and rules

Usage:
    from smartbench.core import (
        UnifiedDiagnosticEngine,
        UnifiedDiagnosticConfig,
        AdapterRegistry,
        RuleRegistry,
        PythonAdapter,
        NullDereferenceRule,
        register_builtin_rules,
    )

    # Setup
    adapters = AdapterRegistry()
    adapters.register(PythonAdapter())

    rules = RuleRegistry()
    register_builtin_rules(rules)

    # Run
    engine = UnifiedDiagnosticEngine(adapters, rules)
    result = engine.diagnose(project_path, config)

    for finding in result.findings:
        print(f"{finding.severity}: {finding.message} at {finding.location}")
"""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from smartbench.analysis import SemanticLinker, StateRuleConfigError, load_state_rule_file
from smartbench.core.adapters.base import AdapterRegistry
from smartbench.core.rules.base import DiagnosticRule, Finding, RuleRegistry
from smartbench.core.rules.state_machine import DeclarativeStateRule
from smartbench.detector import ProjectFingerprint, ProjectScanner
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.ir import (
    CapabilityLevel,
    EvidencePack,
    FactKind,
    OperationEdgeKind,
    SemanticFact,
    SemanticIR,
    SourceRole,
)


@dataclass
class UnifiedDiagnosticConfig:
    """Configuration for a unified diagnostic run."""

    use_llm_rules: bool = False
    use_static_rules: bool = True
    max_files: int = 500
    rule_ids: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    file_paths: Optional[List[Path]] = None
    min_confidence: float = 0.7
    build_evidence_packs: bool = True
    max_evidence_packs: int = 50
    evidence_hops: int = 2
    evidence_nodes: int = 8
    state_rule_paths: Optional[List[Path]] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        if self.max_evidence_packs < 0:
            raise ValueError("max_evidence_packs must be non-negative")
        if self.evidence_hops < 0:
            raise ValueError("evidence_hops must be non-negative")
        if self.evidence_nodes < 0:
            raise ValueError("evidence_nodes must be non-negative")


@dataclass
class UnifiedDiagnosticResult:
    """Result of a unified diagnostic run."""

    # ``ir`` is now the language-neutral SemanticIR.  SemanticIR deliberately
    # delegates graph queries to its legacy CodeGraph so existing integrations
    # continue to work while analyzers migrate to the new contract.
    ir: Optional[SemanticIR] = None
    findings: List[Finding] = field(default_factory=list)
    fingerprint: Optional[ProjectFingerprint] = None
    stats: Dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)
    evidence_packs: Dict[str, EvidencePack] = field(default_factory=dict)
    analysis_status: Dict[str, Dict[str, object]] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "stats": self.stats,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
            "analysis_status": self.analysis_status,
            "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
            "ir_schema_version": self.ir.schema_version if self.ir else None,
            "ir_languages": list(self.ir.languages) if self.ir else [],
            "ir_capabilities": (
                {
                    language: capabilities.to_dict()
                    for language, capabilities in self.ir.capabilities.items()
                }
                if self.ir
                else {}
            ),
            "evidence_packs": {key: pack.to_dict() for key, pack in self.evidence_packs.items()},
        }


class UnifiedDiagnosticEngine:
    """Main diagnostic orchestrator.

    This class coordinates:
      1. Project fingerprinting (language detection)
      2. IR building via appropriate adapter
      3. Rule execution
      4. Finding aggregation
    """

    def __init__(
        self,
        adapter_registry: AdapterRegistry,
        rule_registry: RuleRegistry,
    ):
        self.adapters = adapter_registry
        self.rules = rule_registry

    def diagnose(
        self,
        project_path: Path,
        config: Optional[UnifiedDiagnosticConfig] = None,
    ) -> UnifiedDiagnosticResult:
        """Run the full diagnostic pipeline on a project.

        Args:
            project_path: Root directory of the project
            config: Optional configuration for this run

        Returns:
            UnifiedDiagnosticResult with findings and stats
        """
        start_time = time.time()
        result = UnifiedDiagnosticResult()

        if config is None:
            config = UnifiedDiagnosticConfig()

        try:
            # Step 1: Fingerprint the project
            result.fingerprint = self._fingerprint(project_path)

            # Step 2: Build IR for each detected language
            irs: List[SemanticIR] = []
            detected_langs = self._get_detected_languages(result.fingerprint, config)

            for lang in detected_langs:
                adapter = self.adapters.get_adapter_for_language(lang)
                if adapter:
                    try:
                        ir = adapter.parse_semantic_project(
                            project_path,
                            file_paths=config.file_paths,
                        )
                        irs.append(ir)
                    except Exception as e:
                        result.errors.append(f"Failed to parse {lang}: {e}")

            # Merge IRs if multiple languages
            result.ir = self._merge_irs(irs, project_path)
            self._link_semantics(result)

            # Step 3: Run rules on the IR
            applicable_rules = self._get_applicable_rules(
                detected_langs,
                config,
            )
            applicable_rules = self._with_state_rules(
                applicable_rules,
                detected_langs,
                config,
                result,
            )

            if result.ir:
                for rule in applicable_rules:
                    if rule.requires_llm and not config.use_llm_rules:
                        continue
                    # Skip rules that are disabled by default
                    if not getattr(rule, "enabled_by_default", True) and (
                        not config.rule_ids or rule.rule_id not in config.rule_ids
                    ):
                        continue
                    self._execute_rule(rule, result.ir, result)

            # Step 3.5: Apply confidence threshold filter
            result.findings = self._filter_by_confidence(
                result.findings,
                config.min_confidence,
            )

            if result.ir and config.build_evidence_packs:
                result.evidence_packs = self._build_evidence_packs(result, config)

            # Step 4: Compute stats
            result.stats = self._compute_stats(result, detected_langs)

        except Exception as e:
            result.errors.append(f"Diagnostic failed: {e}")

        result.duration_ms = int((time.time() - start_time) * 1000)
        return result

    def diagnose_file(
        self,
        file_path: Path,
        project_root: Optional[Path] = None,
        config: Optional[UnifiedDiagnosticConfig] = None,
    ) -> UnifiedDiagnosticResult:
        """Run diagnostics on a single file."""
        start_time = time.time()
        result = UnifiedDiagnosticResult()

        if config is None:
            config = UnifiedDiagnosticConfig()

        if project_root is None:
            project_root = file_path.parent

        try:
            # Find adapter for this file
            adapter = self.adapters.get_adapter_for_file(file_path)
            if not adapter:
                result.errors.append(f"No adapter for file: {file_path}")
                result.duration_ms = int((time.time() - start_time) * 1000)
                return result

            # Parse the file
            result.ir = adapter.parse_semantic_file(file_path, project_root)
            self._link_semantics(result)

            # Get applicable rules
            applicable_rules = self._get_applicable_rules(
                [adapter.language],
                config,
            )
            applicable_rules = self._with_state_rules(
                applicable_rules,
                [adapter.language],
                config,
                result,
            )

            # Run rules
            for rule in applicable_rules:
                if rule.requires_llm and not config.use_llm_rules:
                    continue
                # Skip rules that are disabled by default
                if not getattr(rule, "enabled_by_default", True) and (
                    not config.rule_ids or rule.rule_id not in config.rule_ids
                ):
                    continue
                self._execute_rule(rule, result.ir, result)

            # Apply confidence threshold filter
            result.findings = self._filter_by_confidence(
                result.findings,
                config.min_confidence,
            )

            if result.ir and config.build_evidence_packs:
                result.evidence_packs = self._build_evidence_packs(result, config)

            # Compute stats
            result.stats = self._compute_stats(result, [adapter.language])

        except Exception as e:
            result.errors.append(f"Diagnostic failed: {e}")

        result.duration_ms = int((time.time() - start_time) * 1000)
        return result

    def _fingerprint(self, project_path: Path) -> ProjectFingerprint:
        """Fingerprint the project to detect languages and structure."""
        scanner = ProjectScanner(str(project_path))
        return scanner.scan()

    def _get_detected_languages(
        self,
        fingerprint: Optional[ProjectFingerprint],
        config: UnifiedDiagnosticConfig,
    ) -> List[str]:
        """Get the list of languages to process."""
        if config.languages:
            return config.languages

        if fingerprint:
            languages = [fingerprint.primary_language]
            languages.extend(fingerprint.secondary_languages)
            # Convert Language enum to string
            return [lang.value for lang in languages if lang.value != "unknown"]

        return []

    def _get_applicable_rules(
        self,
        languages: List[str],
        config: UnifiedDiagnosticConfig,
    ) -> List[DiagnosticRule]:
        """Get rules applicable to the given languages and config."""
        applicable: List[DiagnosticRule] = []
        seen: Set[str] = set()

        if not config.use_static_rules:
            return applicable

        for lang in languages:
            rules = self.rules.get_rules_for_language(lang)
            for rule in rules:
                if rule.rule_id not in seen:
                    seen.add(rule.rule_id)
                    applicable.append(rule)

        # Filter by rule_ids if specified
        if config.rule_ids:
            applicable = [r for r in applicable if r.rule_id in config.rule_ids]

        return applicable

    def _execute_rule(
        self,
        rule: DiagnosticRule,
        ir: SemanticIR,
        result: UnifiedDiagnosticResult,
    ) -> None:
        """Evaluate a rule contract, run it, and attach provenance metadata."""
        supported = rule.supported_languages
        targets = (
            sorted(set(ir.languages) & set(supported))
            if supported is not None
            else list(ir.languages)
        )
        assessment = ir.assess_requirements(rule.analysis_requirements, targets)
        status = assessment.to_dict()
        roles = rule.source_roles
        if roles is not None:
            status["source_roles"] = sorted(role.value for role in roles)
        result.analysis_status[rule.rule_id] = status

        if assessment.status == CapabilityLevel.UNSUPPORTED:
            missing = {
                language: [
                    capability
                    for capability, detail in details.get("capabilities", {}).items()
                    if isinstance(detail, dict)
                    and detail.get("status") == CapabilityLevel.UNSUPPORTED.value
                ]
                for language, details in assessment.by_language.items()
            }
            missing = {language: values for language, values in missing.items() if values}
            result.errors.append(
                f"Rule {rule.rule_id} skipped: missing semantic capabilities {missing}; "
                f"analysis_status={assessment.status.value}"
            )
            return

        try:
            findings = rule.analyze(ir)
        except Exception as exc:
            result.errors.append(f"Rule {rule.rule_id} failed: {exc}")
            return

        excluded = 0
        for finding in findings:
            role = self._source_role(ir, finding.location.file_path)
            if role is not None:
                finding.metadata.setdefault("source_role", role.value)
                finding.metadata.setdefault("analysis_status", assessment.status.value)
                if assessment.reason:
                    finding.metadata.setdefault("analysis_limitations", assessment.reason)
            if roles is not None and role is not None and role not in roles:
                excluded += 1
                continue
            result.findings.append(finding)
        if excluded:
            status["excluded_findings"] = excluded

    @staticmethod
    def _source_role(ir: SemanticIR, file_path: str) -> Optional[SourceRole]:
        unit = ir.source_units.get(file_path)
        if unit is not None:
            return unit.role
        # A finding may use an absolute path while the IR stores a relative
        # path.  Resolve that mismatch without weakening path safety.
        try:
            relative = str(Path(file_path).resolve().relative_to(Path(ir.project_path).resolve()))
        except (ValueError, OSError):
            relative = ""
        unit = ir.source_units.get(relative) if relative else None
        return unit.role if unit is not None else None

    def _with_state_rules(
        self,
        rules: List[DiagnosticRule],
        languages: List[str],
        config: UnifiedDiagnosticConfig,
        result: UnifiedDiagnosticResult,
    ) -> List[DiagnosticRule]:
        """Load explicitly supplied declarative rules without mutating the registry."""
        combined = list(rules)
        known_ids = {rule.rule_id for rule in combined}
        detected = set(languages)
        for path in config.state_rule_paths or []:
            try:
                definitions = load_state_rule_file(Path(path))
            except (OSError, StateRuleConfigError) as exc:
                result.errors.append(f"State-rule load failed for {path}: {exc}")
                continue
            for definition in definitions:
                if config.rule_ids and definition.rule_id not in config.rule_ids:
                    continue
                if definition.languages and not detected.intersection(definition.languages):
                    continue
                if definition.rule_id in known_ids:
                    result.errors.append(
                        f"State rule {definition.rule_id} skipped: duplicate rule id"
                    )
                    continue
                combined.append(DeclarativeStateRule(definition))
                known_ids.add(definition.rule_id)
        return combined

    def _filter_by_confidence(
        self,
        findings: List[Finding],
        min_confidence: float,
    ) -> List[Finding]:
        """Return findings meeting the configured inclusive threshold."""
        if min_confidence <= 0:
            return findings
        return [finding for finding in findings if finding.confidence >= min_confidence]

    @staticmethod
    def _link_semantics(result: UnifiedDiagnosticResult) -> None:
        """Add conservative cross-file call and synchronization relations."""
        if result.ir is None:
            return
        try:
            linker = SemanticLinker()
            linked = linker.link(result.ir)
            linker.apply(result.ir, linked)
        except Exception as exc:
            result.errors.append(f"Semantic linking failed: {exc}")

    def _merge_irs(
        self,
        irs: List[SemanticIR],
        project_path: Path,
    ) -> Optional[SemanticIR]:
        """Merge multiple language-specific IRs into one."""
        if not irs:
            return None
        if len(irs) == 1:
            return irs[0]

        # Use the semantic merge contract.  The structural graph remains the
        # compatibility backing store, while capabilities and source units are
        # merged explicitly rather than hidden in graph metadata.
        merged = irs[0]
        for ir in irs[1:]:
            merged = merged.merge(ir)

        merged.project_path = str(project_path.resolve())
        merged.meta["project_path"] = merged.project_path
        merged.meta["merged_languages"] = True
        return merged

    def _compute_stats(
        self,
        result: UnifiedDiagnosticResult,
        languages: List[str],
    ) -> Dict[str, int]:
        """Compute statistics for the diagnostic run."""
        stats: Dict[str, int] = {}

        # Count findings by severity
        stats["findings_total"] = len(result.findings)
        stats["findings_error"] = sum(1 for f in result.findings if f.severity.value == "error")
        stats["findings_warning"] = sum(1 for f in result.findings if f.severity.value == "warning")
        stats["findings_info"] = sum(1 for f in result.findings if f.severity.value == "info")

        # Count languages
        stats["languages_detected"] = len(languages)

        # Count nodes/edges if we have IR
        if result.ir:
            stats["ir_nodes"] = len(result.ir.nodes)
            stats["ir_edges"] = len(result.ir.edges)
            stats["ir_operations"] = len(result.ir.operations)
            stats["ir_operation_edges"] = len(result.ir.operation_edges)
            stats["ir_facts"] = len(result.ir.facts)
            contract_meta = result.ir.meta.get("semantic_contract", {})
            stats["ir_contract_errors"] = len(
                contract_meta.get("errors", []) if isinstance(contract_meta, dict) else []
            )
            stats["ir_call_edges"] = sum(
                edge.kind == OperationEdgeKind.CALLS for edge in result.ir.operation_edges
            )
            stats["ir_synchronization_edges"] = sum(
                edge.kind == OperationEdgeKind.SYNCHRONIZES for edge in result.ir.operation_edges
            )
            data_edges = [
                edge
                for edge in result.ir.operation_edges
                if edge.kind == OperationEdgeKind.DATA_DEPENDENCY
            ]
            stats["ir_data_dependency_edges"] = len(data_edges)
            stats["ir_argument_edges"] = sum(
                edge.attributes.get("flow") == "argument_to_parameter" for edge in data_edges
            )
            stats["ir_return_edges"] = sum(
                edge.attributes.get("flow") == "return_to_call" for edge in data_edges
            )

        # Count errors
        stats["errors"] = len(result.errors)
        stats["evidence_packs"] = len(result.evidence_packs)
        statuses = [entry.get("status") for entry in result.analysis_status.values()]
        stats["rules_total"] = len(statuses)
        stats["rules_full"] = sum(1 for status in statuses if status == "full")
        stats["rules_partial"] = sum(1 for status in statuses if status == "partial")
        stats["rules_unsupported"] = sum(
            1 for status in statuses if status == "unsupported"
        )
        stats["findings_excluded_source_scope"] = sum(
            int(entry.get("excluded_findings", 0)) for entry in result.analysis_status.values()
        )

        return stats

    def _build_evidence_packs(
        self,
        result: UnifiedDiagnosticResult,
        config: UnifiedDiagnosticConfig,
    ) -> Dict[str, EvidencePack]:
        """Attach bounded deterministic graph evidence to findings.

        Findings remain backwards-compatible while agents receive an explicit
        evidence channel.  Retrieval failures are recorded as diagnostics, not
        converted into false clean results.
        """
        if not result.ir or config.max_evidence_packs == 0:
            return {}
        try:
            rag = DeterministicGraphRAG(result.ir)
        except Exception as exc:
            result.errors.append(f"Evidence graph unavailable: {exc}")
            return {}

        packs: Dict[str, EvidencePack] = {}
        for finding in result.findings[: config.max_evidence_packs]:
            location = finding.location
            query = f"{location.file_path} {finding.message}"
            try:
                pack = rag.retrieve(
                    query,
                    hops=config.evidence_hops,
                    max_nodes=config.evidence_nodes,
                )
            except Exception as exc:
                result.errors.append(
                    f"Evidence retrieval failed for {location.file_path}:{location.line_start}: {exc}"
                )
                continue
            finding_fact = self._finding_semantic_fact(result.ir, finding)
            if finding_fact is not None:
                pack = EvidencePack.from_facts(
                    query,
                    [finding_fact, *pack.facts],
                    retrieval_trace=(
                        *pack.retrieval_trace,
                        f"finding:{finding.rule_id}",
                    ),
                    graph_version=pack.graph_version,
                )
            key_material = (
                f"{finding.rule_id}|{location.file_path}|{location.line_start}|"
                f"{location.line_end}|{finding.message}"
            )
            key = "finding-" + hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:16]
            packs[key] = pack
            finding.metadata.setdefault("evidence_pack_id", key)
        return packs

    @staticmethod
    def _finding_semantic_fact(
        ir: SemanticIR,
        finding: Finding,
    ) -> Optional[SemanticFact]:
        """Recover exact normalized operations referenced by a deterministic finding."""
        operation_ids = {
            str(finding.metadata.get("event_operation", "")),
            str(finding.metadata.get("action_operation", "")),
        }
        path_operation_ids = finding.metadata.get("path_operations", [])
        if isinstance(path_operation_ids, list):
            operation_ids.update(
                str(operation_id)
                for operation_id in path_operation_ids
                if isinstance(operation_id, str) and operation_id
            )
        operation_ids.discard("")
        if not operation_ids:
            return None
        operations = [operation for operation in ir.operations if operation.id in operation_ids]
        if not operations:
            return None
        operations.sort(
            key=lambda operation: (
                operation.location.file_path,
                operation.location.line_start,
                operation.id,
            )
        )
        attributes = {
            "rule_id": finding.rule_id,
            "operation_ids": [operation.id for operation in operations],
            "missing": finding.metadata.get("missing", ""),
        }
        if finding.metadata.get("scope") == "interprocedural":
            attributes.update(
                {
                    "proof_scope": "interprocedural",
                    "path_arcs": finding.metadata.get("path_arcs", []),
                    "max_call_depth": finding.metadata.get("max_call_depth", 0),
                }
            )
        return SemanticFact(
            subject=str(finding.metadata.get("scope_id", finding.rule_id)),
            predicate=FactKind.STATE_TRANSITION,
            object=finding.message,
            evidence=tuple(operation.location for operation in operations),
            confidence=finding.confidence,
            attributes=attributes,
        )
