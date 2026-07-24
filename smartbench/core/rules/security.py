"""
Security-related diagnostic rules (language-agnostic).

This module provides security vulnerability detection rules.
NOTE: The regex-based rules (PathTraversal, CommandInjection) are deprecated
in favor of data-flow analysis rules which provide much lower false positive rates.
"""

import re
from typing import Dict, List, Tuple

from smartbench.core.rules.base import (
    DiagnosticRule,
    Finding,
    Location,
    Severity,
)
from smartbench.graph.schema import CodeGraph


class CommandInjectionRule(DiagnosticRule):
    """
    Detects potential command injection vulnerabilities.

    DEPRECATED: This regex-based rule produces high false positive rates.
    Use DataFlowCommandInjectionRule instead for deterministic analysis.
    """

    # Mark as disabled by default due to high false positive rate
    enabled_by_default: bool = False

    @property
    def rule_id(self) -> str:
        return "command_injection"

    @property
    def rule_name(self) -> str:
        return "Potential Command Injection"

    @property
    def severity(self) -> Severity:
        return Severity.ERROR

    @property
    def description(self) -> str:
        return "Detects potential command injection vulnerabilities (regex-based, use data-flow version instead)"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        # Use the helper method to collect source files
        source_files = self._collect_source_files(ir)

        for file_path, language in source_files:
            source = self._read_source(ir, file_path)
            if not source:
                continue

            patterns = self._find_command_patterns(source, file_path, language)
            findings.extend(patterns)

        return findings

    def _find_command_patterns(self, source: str, file_path: str, language: str) -> List[Finding]:
        findings: List[Finding] = []

        patterns: Dict[str, List[Tuple[str, str, float]]] = {
            "typescript": [
                # More strict patterns - require variable interpolation
                (
                    r"(?:child_process\.(?:exec|execSync))\s*\(\s*[`'\"][^`'\"]*\$\{[^}]+}",
                    "Potential command injection with template string interpolation",
                    0.7,
                ),  # Higher confidence
                (
                    r"(?:child_process\.(?:spawn|spawnSync))\s*\(\s*[`'\"][^`'\"]*\$\{[^}]+}",
                    "Potential command injection with spawn and dynamic argument",
                    0.6,
                ),
            ],
            "python": [
                (
                    r"(?:os\.system|subprocess\.(?:call|check_output|check_call|run|Popen))\s*\(\s*f[\"']",
                    "Potential command injection with f-string",
                    0.8,
                ),
                (
                    r"(?:os\.popen|os\.popen2|os\.popen3|os\.popen4)\s*\(\s*f[\"']",
                    "Potential command injection with popen and f-string",
                    0.7,
                ),
            ],
        }

        lang_patterns = patterns.get(language, [])
        for pattern, message, confidence in lang_patterns:
            for match in re.finditer(pattern, source):
                line_no = source[: match.start()].count("\n") + 1

                # Additional context check - skip if it's in a comment or import
                lines = source.split("\n")
                if line_no <= len(lines):
                    line_content = lines[line_no - 1]
                    stripped = line_content.strip()
                    if stripped.startswith(("//", "/*", "*", "#")):
                        continue  # Skip comments
                    if stripped.startswith(("import ", "from ", "require(")):
                        continue  # Skip imports

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
                    confidence=confidence,
                )
                findings.append(finding)

        return findings


class PathTraversalRule(DiagnosticRule):
    """
    Detects potential path traversal vulnerabilities.

    DEPRECATED: This regex-based rule produces extremely high false positive rates.
    Use DataFlowPathTraversalRule instead for deterministic analysis.
    """

    # Mark as disabled by default due to extremely high false positive rate
    enabled_by_default: bool = False

    @property
    def rule_id(self) -> str:
        return "path_traversal"

    @property
    def rule_name(self) -> str:
        return "Potential Path Traversal"

    @property
    def severity(self) -> Severity:
        return Severity.ERROR

    @property
    def description(self) -> str:
        return "Detects potential path traversal vulnerabilities (regex-based, use data-flow version instead)"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        # Use the helper method to collect source files
        source_files = self._collect_source_files(ir)

        for file_path, language in source_files:
            source = self._read_source(ir, file_path)
            if not source:
                continue

            patterns = self._find_path_patterns(source, file_path, language)
            findings.extend(patterns)

        return findings

    def _find_path_patterns(self, source: str, file_path: str, language: str) -> List[Finding]:
        findings: List[Finding] = []

        # Process line by line for better context analysis
        lines = source.split("\n")

        for line_idx, line_content in enumerate(lines, 1):
            # Quick check - skip if no path traversal pattern
            if "../" not in line_content and "..\\" not in line_content:
                continue

            # Filter 1: Skip common safe contexts
            stripped = line_content.strip()

            # Skip comments
            if stripped.startswith(("//", "/*", "*", "#", "<!--")):
                continue

            # Skip import/export statements
            if stripped.startswith(("import ", "export ", "from ", "require(")):
                continue

            # Skip lines that are just string literals in definitions
            if (
                stripped.startswith(('"', "'", "`"))
                and stripped.endswith(('"', "'", "`"))
                and "../" in stripped
            ):
                # This is likely just a constant string path, not user-controlled
                continue

            # Filter 2: Only look for actual dangerous file operations
            # Check if we're in a real file operation context
            # This is more strict than just checking for "path" or "file"
            has_dangerous_file_op = False

            # Look for actual function calls that take paths
            dangerous_patterns = [
                # JavaScript/TypeScript
                "fs.readFile",
                "fs.writeFile",
                "fs.open",
                "fs.readFileSync",
                "fs.writeFileSync",
                "fs.createReadStream",
                "fs.createWriteStream",
                "fs.promises",
                "readFile(",
                "writeFile(",
                "open(",
                # Python
                "open(",
                "pathlib.",
                "os.open",
                "os.read",
                "os.write",
            ]

            for pattern in dangerous_patterns:
                if pattern in line_content:
                    has_dangerous_file_op = True
                    break

            # Also check for template strings with ../ and variables
            if "${" in line_content and ("../" in line_content or "..\\" in line_content):
                has_dangerous_file_op = True

            if not has_dangerous_file_op:
                continue

            # Filter 3: Check if the ../ appears to be user-controlled
            # If it's in a template string with ${...}, it's more suspicious
            is_suspicious = False
            confidence = 0.5

            if "${" in line_content and "../" in line_content:
                is_suspicious = True
                confidence = 0.7
            elif ".." in line_content and ("req." in line_content or "request." in line_content):
                is_suspicious = True
                confidence = 0.8

            if is_suspicious:
                finding = Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    location=Location(
                        file_path=file_path,
                        line_start=line_idx,
                        line_end=line_idx,
                    ),
                    message="Potential path traversal with untrusted input",
                    confidence=confidence,
                )
                findings.append(finding)

        return findings


class HardcodedSecretRule(DiagnosticRule):
    """Detects potential hardcoded secrets."""

    @property
    def rule_id(self) -> str:
        return "hardcoded_secret"

    @property
    def rule_name(self) -> str:
        return "Potential Hardcoded Secret"

    @property
    def severity(self) -> Severity:
        return Severity.WARNING

    @property
    def description(self) -> str:
        return "Detects potential hardcoded secrets and API keys"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        # Use the helper method to collect source files
        source_files = self._collect_source_files(ir)

        for file_path, language in source_files:
            source = self._read_source(ir, file_path)
            if source:
                patterns = self._find_secret_patterns(source, file_path)
                findings.extend(patterns)

        return findings

    def _find_secret_patterns(self, source: str, file_path: str) -> List[Finding]:
        findings: List[Finding] = []

        path_lower = file_path.lower()
        is_test_file = any(
            test_pattern in path_lower
            for test_pattern in [
                ".spec.",
                ".test.",
                ".vitest.",
                "/__tests__/",
                "/test/",
                "/tests/",
                "_spec.",
                "_test.",
                "spec_",
                "test_",
            ]
        )

        # Common secret patterns
        secret_patterns = [
            (
                r"(?i)(?:api[_-]?key|secret[_-]?key|password|passwd|private[_-]?key|access[_-]?token)\s*[=:]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]",
                "Potential hardcoded API key or secret",
                0.7,
            ),
            (r"(?i)gh[pousr]_[A-Za-z0-9_]{36,251}", "Potential hardcoded GitHub token", 0.8),
            (r"(?i)sk-[a-zA-Z0-9]{48}", "Potential hardcoded OpenAI API key", 0.9),
            (
                r"(?i)-----BEGIN\s+(?:RSA|DSA|EC|PGP|OPENSSH)\s+(?:PRIVATE|ENCRYPTED)\s+KEY-----",
                "Potential hardcoded private key",
                0.9,
            ),
        ]

        for pattern, message, confidence in secret_patterns:
            for match in re.finditer(pattern, source):
                line_no = source[: match.start()].count("\n") + 1

                # Skip if it's in a comment
                lines = source.split("\n")
                if line_no <= len(lines):
                    line_content = lines[line_no - 1]
                    stripped = line_content.strip()
                    if stripped.startswith(("//", "/*", "*", "#")):
                        continue

                matched_text = match.group(0).lower()
                placeholder_markers = (
                    "changeme",
                    "dummy",
                    "example",
                    "fake",
                    "placeholder",
                    "test-",
                    "test_",
                    "your-",
                    "your_",
                )
                adjusted_confidence = confidence
                if any(marker in matched_text for marker in placeholder_markers):
                    adjusted_confidence = min(adjusted_confidence, 0.2)
                elif is_test_file and message == "Potential hardcoded API key or secret":
                    adjusted_confidence = min(adjusted_confidence, 0.4)

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
                    confidence=adjusted_confidence,
                )
                findings.append(finding)

        return findings


def register_security_rules(registry):
    """
    Register security rules.

    NOTE: Regex-based rules with high false-positive rates are NOT registered
    by default. They can be manually enabled if needed.
    """
    # Only register HardcodedSecretRule by default
    registry.register(HardcodedSecretRule())

    # The following rules are deprecated due to high false positive rates:
    # - CommandInjectionRule (use DataFlowCommandInjectionRule instead)
    # - PathTraversalRule (use DataFlowPathTraversalRule instead)
    #
    # To enable them (not recommended):
    # registry.register(CommandInjectionRule())
    # registry.register(PathTraversalRule())
