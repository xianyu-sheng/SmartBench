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

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from smartbench.core.adapters.base import AdapterRegistry
from smartbench.core.rules.base import DiagnosticRule, Finding, RuleRegistry
from smartbench.detector import ProjectFingerprint, ProjectScanner
from smartbench.graph.schema import CodeGraph


@dataclass
class UnifiedDiagnosticConfig:
    """Configuration for a unified diagnostic run."""
    use_llm_rules: bool = False
    use_static_rules: bool = True
    max_files: int = 500
    rule_ids: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    file_paths: Optional[List[Path]] = None


@dataclass
class UnifiedDiagnosticResult:
    """Result of a unified diagnostic run."""
    ir: Optional[CodeGraph] = None
    findings: List[Finding] = field(default_factory=list)
    fingerprint: Optional[ProjectFingerprint] = None
    stats: Dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "stats": self.stats,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
            "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
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
            irs: List[CodeGraph] = []
            detected_langs = self._get_detected_languages(result.fingerprint, config)

            for lang in detected_langs:
                adapter = self.adapters.get_adapter_for_language(lang)
                if adapter:
                    try:
                        ir = adapter.parse_project(
                            project_path,
                            file_paths=config.file_paths,
                        )
                        irs.append(ir)
                    except Exception as e:
                        result.errors.append(f"Failed to parse {lang}: {e}")

            # Merge IRs if multiple languages
            result.ir = self._merge_irs(irs, project_path)

            # Step 3: Run rules on the IR
            applicable_rules = self._get_applicable_rules(
                detected_langs,
                config,
            )

            if result.ir:
                for rule in applicable_rules:
                    if rule.requires_llm and not config.use_llm_rules:
                        continue
                    try:
                        findings = rule.analyze(result.ir)
                        result.findings.extend(findings)
                    except Exception as e:
                        result.errors.append(f"Rule {rule.rule_id} failed: {e}")

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
            result.ir = adapter.parse_file(file_path, project_root)

            # Get applicable rules
            applicable_rules = self._get_applicable_rules(
                [adapter.language],
                config,
            )

            # Run rules
            for rule in applicable_rules:
                if rule.requires_llm and not config.use_llm_rules:
                    continue
                try:
                    findings = rule.analyze(result.ir)
                    result.findings.extend(findings)
                except Exception as e:
                    result.errors.append(f"Rule {rule.rule_id} failed: {e}")

            # Compute stats
            result.stats = self._compute_stats(result, [adapter.language])

        except Exception as e:
            result.errors.append(f"Diagnostic failed: {e}")

        result.duration_ms = int((time.time() - start_time) * 1000)
        return result

    def _fingerprint(self, project_path: Path) -> ProjectFingerprint:
        """Fingerprint the project to detect languages and structure."""
        scanner = ProjectScanner()
        return scanner.scan(project_path)

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
            return [
                lang.value for lang in languages
                if lang.value != "unknown"
            ]

        return []

    def _get_applicable_rules(
        self,
        languages: List[str],
        config: UnifiedDiagnosticConfig,
    ) -> List[DiagnosticRule]:
        """Get rules applicable to the given languages and config."""
        applicable: List[DiagnosticRule] = []
        seen: Set[str] = set()

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

    def _merge_irs(
        self,
        irs: List[CodeGraph],
        project_path: Path,
    ) -> Optional[CodeGraph]:
        """Merge multiple language-specific IRs into one."""
        if not irs:
            return None
        if len(irs) == 1:
            return irs[0]

        # Use the existing merge functionality
        merged = irs[0]
        for ir in irs[1:]:
            merged = merged.merge(ir)

        merged.meta["project_path"] = str(project_path.resolve())
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
        stats["findings_error"] = sum(
            1 for f in result.findings if f.severity.value == "error"
        )
        stats["findings_warning"] = sum(
            1 for f in result.findings if f.severity.value == "warning"
        )
        stats["findings_info"] = sum(
            1 for f in result.findings if f.severity.value == "info"
        )

        # Count languages
        stats["languages_detected"] = len(languages)

        # Count nodes/edges if we have IR
        if result.ir:
            stats["ir_nodes"] = len(result.ir.nodes)
            stats["ir_edges"] = len(result.ir.edges)

        # Count errors
        stats["errors"] = len(result.errors)

        return stats
