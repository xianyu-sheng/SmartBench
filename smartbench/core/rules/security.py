"""
Security-related diagnostic rules (language-agnostic).
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from smartbench.core.rules.base import (
    DiagnosticRule,
    Finding,
    Location,
    Severity,
)
from smartbench.graph.schema import CodeGraph, NodeType


class CommandInjectionRule(DiagnosticRule):
    """Detects potential command injection vulnerabilities."""

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
        return "Detects potential command injection vulnerabilities"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []
        func_nodes = [n for n in ir.nodes.values() if n.node_type == NodeType.FUNCTION]

        for fn in func_nodes:
            source = self._read_source(ir, fn.file_path)
            if not source:
                continue

            patterns = self._find_command_patterns(source, fn.file_path, fn.language)
            findings.extend(patterns)

        return findings

    def _read_source(self, ir: CodeGraph, file_path: str) -> Optional[str]:
        try:
            project_path = ir.meta.get("project_path")
            if project_path:
                full_path = Path(project_path) / file_path
                from smartbench.path_safety import read_text_bounded
                return read_text_bounded(full_path, 2 * 1024 * 1024)
        except Exception:
            pass
        return None

    def _find_command_patterns(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        findings: List[Finding] = []

        patterns: Dict[str, List[Tuple[str, str]]] = {
            "python": [
                (r"(?:os\.system|subprocess\.(?:call|check_output|check_call|run|Popen))\s*\(\s*f?",
                 "Potential command injection with dynamic command",
                ),
                (r"(?:os\.popen|os\.popen2|os\.popen3|os\.popen4)\s*\(",
                 "Potential command injection with popen",
                ),
            ],
            "javascript": [
                (r"(?:child_process\.(?:exec|execSync|spawn|spawnSync|execFile|execFileSync)|eval)\s*\(",
                 "Potential command injection",
                ),
            ],
            "typescript": [
                (r"(?:child_process\.(?:exec|execSync|spawn|spawnSync|execFile|execFileSync)|eval)\s*\(",
                 "Potential command injection",
                ),
            ],
            "go": [
                (r"(?:os\.Exec|exec\.Command|syscall\.Exec)\s*\(",
                 "Potential command injection with exec",
                ),
            ],
            "java": [
                (r"(?:Runtime\.getRuntime\(\)\.exec|ProcessBuilder)\s*\(",
                 "Potential command injection",
                ),
            ],
            "php": [
                (r"(?:exec|system|shell_exec|passthru|popen|proc_open)\s*\(",
                 "Potential command injection",
                ),
            ],
        }

        lang_patterns = patterns.get(language, [])
        for pattern, message in lang_patterns:
            for match in re.finditer(pattern, source):
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


class PathTraversalRule(DiagnosticRule):
    """Detects potential path traversal vulnerabilities."""

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
        return "Detects potential path traversal vulnerabilities"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []
        func_nodes = [n for n in ir.nodes.values() if n.node_type == NodeType.FUNCTION]

        for fn in func_nodes:
            source = self._read_source(ir, fn.file_path)
            if not source:
                continue

            patterns = self._find_path_patterns(source, fn.file_path, fn.language)
            findings.extend(patterns)

        return findings

    def _read_source(self, ir: CodeGraph, file_path: str) -> Optional[str]:
        try:
            project_path = ir.meta.get("project_path")
            if project_path:
                full_path = Path(project_path) / file_path
                from smartbench.path_safety import read_text_bounded
                return read_text_bounded(full_path, 2 * 1024 * 1024)
        except Exception:
            pass
        return None

    def _find_path_patterns(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        findings: List[Finding] = []

        patterns: Dict[str, List[Tuple[str, str]]] = {
            "python": [
                (r"open\s*\(\s*(?:f?[\"']|\w*[^.][\"']).*\.\.(?:/|\\)",
                 "Potential path traversal with open()",
                ),
                (r"(?:os\.path\.join|Path)\s*\(.*\.\.(?:/|\\)",
                 "Potential path traversal with path join",
                ),
            ],
        }

        # Generic pattern for any language
        for match in re.finditer(r"\.\.(?:/|\\)", source):
            line_no = source[:match.start()].count("\n") + 1
            # Look at context around the match
            lines = source.split("\n")
            if line_no <= len(lines):
                line_content = lines[line_no - 1]
                # Only flag if it looks like a path being used with file operations
                if any(keyword in line_content for keyword in ["open", "read", "write", "file", "path"]):
                    finding = Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        location=Location(
                            file_path=file_path,
                            line_start=line_no,
                            line_end=line_no,
                        ),
                        message="Potential path traversal with ../",
                        confidence=0.5,
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
        func_nodes = [n for n in ir.nodes.values() if n.node_type == NodeType.FUNCTION]

        # Also check the entire file
        files: Dict[str, str] = {}
        for n in ir.nodes.values():
            if n.file_path not in files:
                source = self._read_source(ir, n.file_path)
                if source:
                    files[n.file_path] = source

        for file_path, source in files.items():
            patterns = self._find_secret_patterns(source, file_path)
            findings.extend(patterns)

        return findings

    def _read_source(self, ir: CodeGraph, file_path: str) -> Optional[str]:
        try:
            project_path = ir.meta.get("project_path")
            if project_path:
                full_path = Path(project_path) / file_path
                from smartbench.path_safety import read_text_bounded
                return read_text_bounded(full_path, 2 * 1024 * 1024)
        except Exception:
            pass
        return None

    def _find_secret_patterns(self, source: str, file_path: str) -> List[Finding]:
        findings: List[Finding] = []

        # Common secret patterns
        secret_patterns = [
            (r"(?i)(?:api[_-]?key|secret[_-]?key|password|passwd|private[_-]?key|access[_-]?token)\s*[=:]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]",
             "Potential hardcoded API key or secret",
             0.7,
            ),
            (r"(?i)gh[pousr]_[A-Za-z0-9_]{36,251}",
             "Potential hardcoded GitHub token",
             0.8,
            ),
            (r"(?i)sk-[a-zA-Z0-9]{48}",
             "Potential hardcoded OpenAI API key",
             0.9,
            ),
            (r"(?i)-----BEGIN\s+(?:RSA|DSA|EC|PGP|OPENSSH)\s+(?:PRIVATE|ENCRYPTED)\s+KEY-----",
             "Potential hardcoded private key",
             0.9,
            ),
        ]

        for pattern, message, confidence in secret_patterns:
            for match in re.finditer(pattern, source):
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
                    confidence=confidence,
                )
                findings.append(finding)

        return findings


def register_security_rules(registry):
    """Register all security rules."""
    registry.register(CommandInjectionRule())
    registry.register(PathTraversalRule())
    registry.register(HardcodedSecretRule())
