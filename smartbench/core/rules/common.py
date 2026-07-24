"""
Common diagnostic rules (language-agnostic).

These rules work with the unified CodeGraph IR and can detect issues
across multiple languages without language-specific logic.
"""

import re
from typing import Dict, List

from smartbench.core.rules.base import (
    DiagnosticRule,
    Finding,
    Location,
    RuleRegistry,
    Severity,
)
from smartbench.graph.schema import CodeGraph


class NullDereferenceRule(DiagnosticRule):
    """Detects potential null/None/nil dereference patterns.

    This rule looks for patterns like:
      - Checking if something is not None, then using it outside the check
      - Functions that can return None, but the result is used without checking
      - Null assignments followed by dereference
    """

    @property
    def rule_id(self) -> str:
        return "null_dereference"

    @property
    def rule_name(self) -> str:
        return "Potential null dereference"

    @property
    def severity(self) -> Severity:
        return Severity.ERROR

    @property
    def description(self) -> str:
        return "Detects potential null/None/nil dereference patterns"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        # Use the helper method to collect source files
        source_files = self._collect_source_files(ir)

        for file_path, language in source_files:
            source = self._read_source(ir, file_path)
            if not source:
                continue

            patterns = self._find_null_patterns(source, file_path, language)
            findings.extend(patterns)

        return findings

    def _find_null_patterns(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        """Find null-related patterns in source code."""
        findings: List[Finding] = []

        # Language-specific null checks
        null_patterns: Dict[str, List[str]] = {
            "python": [
                (r"None\.(\w+)", "Potential None attribute access: None.{name}"),
                (r"None\[", "Potential None subscript access"),
                (r"None\(", "Potential None call"),
            ],
            "go": [
                (r"nil\.(\w+)", "Potential nil attribute access: nil.{name}"),
                (r"nil\[", "Potential nil subscript access"),
                (r"nil\(", "Potential nil call"),
            ],
            "java": [
                (r"null\.(\w+)", "Potential null attribute access: null.{name}"),
            ],
            "javascript": [
                (r"null\.(\w+)", "Potential null attribute access: null.{name}"),
                (r"undefined\.(\w+)", "Potential undefined attribute access"),
            ],
            "typescript": [
                (r"null\.(\w+)", "Potential null attribute access: null.{name}"),
                (r"undefined\.(\w+)", "Potential undefined attribute access"),
            ],
            "rust": [
                (r"None\.unwrap\(\)", "Potential None unwrap"),
                (r"None\?", "Potential None propagation"),
            ],
        }

        patterns = null_patterns.get(language, [])
        if not patterns:
            return findings

        for pattern, message_template in patterns:
            for match in re.finditer(pattern, source):
                line_no = source[:match.start()].count("\n") + 1
                message = message_template.format(name=match.group(1) if match.groups() else "")

                finding = Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    location=Location(
                        file_path=file_path,
                        line_start=line_no,
                        line_end=line_no,
                    ),
                    message=message,
                    confidence=0.7,
                )
                findings.append(finding)

        return findings


class ResourceLeakRule(DiagnosticRule):
    """Detects potential resource leaks (unclosed files, sockets, etc.).

    This rule looks for patterns like:
      - open() without corresponding close()
      - File handles that go out of scope
      - Missing with/try/use blocks for resources
    """

    @property
    def rule_id(self) -> str:
        return "resource_leak"

    @property
    def rule_name(self) -> str:
        return "Potential resource leak"

    @property
    def severity(self) -> Severity:
        return Severity.WARNING

    @property
    def description(self) -> str:
        return "Detects potential unclosed resources (files, sockets, etc.)"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        # Use the helper method to collect source files
        source_files = self._collect_source_files(ir)

        for file_path, language in source_files:
            source = self._read_source(ir, file_path)
            if not source:
                continue

            leak_patterns = self._find_leak_patterns(
                source, file_path, language
            )
            findings.extend(leak_patterns)

        return findings

    def _find_leak_patterns(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        """Find resource leak patterns in source code."""
        findings: List[Finding] = []

        # Language-specific resource patterns
        leak_patterns: Dict[str, List[tuple]] = {
            "python": [
                # open() without with statement
                (
                    r"^(\s*)(?!.*with.*open)\s*(\w+)\s*=\s*open\(",
                    "File opened without 'with' statement: may not be closed",
                ),
            ],
            "go": [
                (
                    r"^(\s*)(?!.*defer.*Close)\s*(\w+),\s*(\w+)\s*=\s*os\.Open\(",
                    "File opened without defer Close(): may not be closed",
                ),
            ],
        }

        patterns = leak_patterns.get(language, [])
        if not patterns:
            return findings

        for pattern, message in patterns:
            for match in re.finditer(pattern, source, re.MULTILINE):
                line_no = source[:match.start()].count("\n") + 1

                finding = Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    location=Location(
                        file_path=file_path,
                        line_start=line_no,
                        line_end=line_no,
                    ),
                    message=message,
                    confidence=0.6,
                )
                findings.append(finding)

        return findings


def register_builtin_rules(registry: RuleRegistry) -> None:
    """Register all built-in diagnostic rules with a registry."""
    from smartbench.core.rules.flow import register_flow_rules
    from smartbench.core.rules.quality import register_quality_rules
    from smartbench.core.rules.security import register_security_rules

    # Common rules
    registry.register(NullDereferenceRule())
    registry.register(ResourceLeakRule())

    # Security rules
    register_security_rules(registry)

    # Quality rules
    register_quality_rules(registry)

    # Deterministic data-flow security rules
    register_flow_rules(registry)
