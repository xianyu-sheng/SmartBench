"""
Code quality diagnostic rules (language-agnostic).
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


class TodoFixmeRule(DiagnosticRule):
    """Detects TODO/FIXME comments that need attention."""

    @property
    def rule_id(self) -> str:
        return "todo_fixme"

    @property
    def rule_name(self) -> str:
        return "TODO/FIXME Comment"

    @property
    def severity(self) -> Severity:
        return Severity.INFO

    @property
    def description(self) -> str:
        return "Detects TODO and FIXME comments"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        files: Dict[str, str] = {}
        for n in ir.nodes.values():
            if n.file_path not in files:
                source = self._read_source(ir, n.file_path)
                if source:
                    files[n.file_path] = source

        for file_path, source in files.items():
            patterns = self._find_comments(source, file_path)
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

    def _find_comments(self, source: str, file_path: str) -> List[Finding]:
        findings: List[Finding] = []

        comment_patterns = [
            (r"(?://|#|/\*|<!--)\s*(TODO|FIXME|XXX|HACK|BUG):?\s*(.*)", Severity.WARNING, "FIXME/BUG comment"),
            (r"(?://|#|/\*|<!--)\s*(TODO|HACK):?\s*(.*)", Severity.INFO, "TODO/HACK comment"),
        ]

        for pattern, severity, base_message in comment_patterns:
            for match in re.finditer(pattern, source):
                line_no = source[:match.start()].count("\n") + 1
                todo_type = match.group(1)
                message = match.group(2).strip() if match.group(2) else ""

                finding = Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=severity,
                    location=Location(
                        file_path=file_path,
                        line_start=line_no,
                        line_end=line_no,
                    ),
                    message=f"{todo_type}: {message}" if message else todo_type,
                    confidence=1.0,
                )
                findings.append(finding)

        return findings


class UnusedImportRule(DiagnosticRule):
    """Detects potentially unused imports."""

    @property
    def rule_id(self) -> str:
        return "unused_import"

    @property
    def rule_name(self) -> str:
        return "Potentially Unused Import"

    @property
    def severity(self) -> Severity:
        return Severity.INFO

    @property
    def description(self) -> str:
        return "Detects potentially unused imports"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        files: Dict[str, str] = {}
        for n in ir.nodes.values():
            if n.file_path not in files:
                source = self._read_source(ir, n.file_path)
                if source:
                    files[n.file_path] = source

        for file_path, source in files.items():
            patterns = self._find_unused_imports(source, file_path)
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

    def _find_unused_imports(self, source: str, file_path: str) -> List[Finding]:
        findings: List[Finding] = []

        # Simple heuristic for Python: look for imports and see if the name is used
        if file_path.endswith(".py"):
            import_pattern = r"^(?:\s*from\s+(\w+)\s+import\s+(\w+)|import\s+(\w+))"
            imports = []

            lines = source.split("\n")
            for i, line in enumerate(lines):
                match = re.match(import_pattern, line)
                if match:
                    name = match.group(2) or match.group(3)
                    if name and not line.strip().startswith("#"):
                        imports.append((name, i + 1))

            # Check if imported names are used elsewhere
            for name, line_no in imports:
                if name and len(name) > 1:
                    usage_pattern = rf"\b{re.escape(name)}\b"
                    # Count occurrences - at least 2: the import and a usage
                    count = len(re.findall(usage_pattern, source))
                    if count <= 1:
                        # Also check if it's used in a dotted access
                        dotted_count = len(re.findall(rf"\b{re.escape(name)}\.", source))
                        if dotted_count == 0:
                            finding = Finding(
                                rule_id=self.rule_id,
                                rule_name=self.rule_name,
                                severity=self.severity,
                                location=Location(
                                    file_path=file_path,
                                    line_start=line_no,
                                    line_end=line_no,
                                ),
                                message=f"Import '{name}' appears to be unused",
                                confidence=0.6,
                            )
                            findings.append(finding)

        return findings


class ExceptionTooBroadRule(DiagnosticRule):
    """Detects overly broad exception handling."""

    @property
    def rule_id(self) -> str:
        return "broad_exception"

    @property
    def rule_name(self) -> str:
        return "Overly Broad Exception"

    @property
    def severity(self) -> Severity:
        return Severity.WARNING

    @property
    def description(self) -> str:
        return "Detects overly broad exception handling"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []
        func_nodes = [n for n in ir.nodes.values() if n.node_type == NodeType.FUNCTION]

        for fn in func_nodes:
            source = self._read_source(ir, fn.file_path)
            if not source:
                continue

            patterns = self._find_broad_exceptions(source, fn.file_path, fn.language)
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

    def _find_broad_exceptions(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        findings: List[Finding] = []

        patterns: Dict[str, List[Tuple[str, str]]] = {
            "python": [
                (r"except\s*:\s*(?:#|$|//)",
                 "Bare 'except:' catches all exceptions",
                ),
                (r"except\s+(?:Exception|BaseException)\s*[:,)]",
                 "Catching too broad exception type",
                ),
            ],
            "java": [
                (r"catch\s*\(\s*(?:Exception|Throwable)\s+\w+\s*\)",
                 "Catching too broad exception type",
                ),
            ],
            "go": [
                (r"recover\s*\(\)",
                 "Using recover() without type assertion",
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
                    confidence=0.7,
                )
                findings.append(finding)

        return findings


class InsecureRandomRule(DiagnosticRule):
    """Detects use of insecure random number generators."""

    @property
    def rule_id(self) -> str:
        return "insecure_random"

    @property
    def rule_name(self) -> str:
        return "Insecure Random Number Generator"

    @property
    def severity(self) -> Severity:
        return Severity.WARNING

    @property
    def description(self) -> str:
        return "Detects use of insecure random number generators for security purposes"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []
        func_nodes = [n for n in ir.nodes.values() if n.node_type == NodeType.FUNCTION]

        for fn in func_nodes:
            source = self._read_source(ir, fn.file_path)
            if not source:
                continue

            patterns = self._find_insecure_random(source, fn.file_path, fn.language)
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

    def _find_insecure_random(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        findings: List[Finding] = []

        patterns: Dict[str, List[Tuple[str, str]]] = {
            "python": [
                (r"(?:random\.random|random\.randint|random\.randrange|random\.choice|random\.shuffle)\s*\(",
                 "Using insecure random module - use secrets module for security-sensitive operations",
                ),
            ],
            "java": [
                (r"new\s+Random\s*\(",
                 "Using insecure Random class - use SecureRandom for security-sensitive operations",
                ),
            ],
            "go": [
                (r"math/rand\.(?:Intn|Int|Int31|Int63|Float32|Float64|Shuffle|Read)",
                 "Using insecure math/rand - use crypto/rand for security-sensitive operations",
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
                    confidence=0.7,
                )
                findings.append(finding)

        return findings


class SqlInjectionRule(DiagnosticRule):
    """Detects potential SQL injection vulnerabilities."""

    @property
    def rule_id(self) -> str:
        return "sql_injection"

    @property
    def rule_name(self) -> str:
        return "Potential SQL Injection"

    @property
    def severity(self) -> Severity:
        return Severity.ERROR

    @property
    def description(self) -> str:
        return "Detects potential SQL injection vulnerabilities"

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []
        func_nodes = [n for n in ir.nodes.values() if n.node_type == NodeType.FUNCTION]

        for fn in func_nodes:
            source = self._read_source(ir, fn.file_path)
            if not source:
                continue

            patterns = self._find_sql_patterns(source, fn.file_path, fn.language)
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

    def _find_sql_patterns(
        self, source: str, file_path: str, language: str
    ) -> List[Finding]:
        findings: List[Finding] = []

        patterns: Dict[str, List[Tuple[str, str]]] = {
            "python": [
                (r"(?:execute|query|run)\s*\(\s*f[\"'].*(?:\+|%s|%d|format)",
                 "Potential SQL injection with formatted string",
                ),
                (r"(?:execute|query|run)\s*\(\s*[\"'].*\".*\s*\+",
                 "Potential SQL injection with string concatenation",
                ),
            ],
            "javascript": [
                (r"(?:query|execute|all|run)\s*\(\s*[`\"'].*\$\{[^}]*\}",
                 "Potential SQL injection with template literal",
                ),
                (r"(?:query|execute|all|run)\s*\(\s*[^)]*\s*\+",
                 "Potential SQL injection with string concatenation",
                ),
            ],
            "typescript": [
                (r"(?:query|execute|all|run)\s*\(\s*[`\"'].*\$\{[^}]*\}",
                 "Potential SQL injection with template literal",
                ),
                (r"(?:query|execute|all|run)\s*\(\s*[^)]*\s*\+",
                 "Potential SQL injection with string concatenation",
                ),
            ],
            "go": [
                (r"Query(?:Context|Row|RowContext)?\s*\(\s*[^,]+\+",
                 "Potential SQL injection with string concatenation",
                ),
                (r"Exec(?:Context)?\s*\(\s*[^,]+\+",
                 "Potential SQL injection with string concatenation",
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


def register_quality_rules(registry):
    """Register all quality rules."""
    registry.register(TodoFixmeRule())
    registry.register(UnusedImportRule())
    registry.register(ExceptionTooBroadRule())
    registry.register(InsecureRandomRule())
    registry.register(SqlInjectionRule())
