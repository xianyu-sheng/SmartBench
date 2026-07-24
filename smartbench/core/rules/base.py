"""
Base rule definitions for the unified diagnostic framework.

The key abstractions:
  - Severity: How important a finding is
  - Finding: A detected issue with evidence
  - DiagnosticRule: Base class for writing diagnostic rules
  - RuleRegistry: Register and retrieve rules
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from smartbench.ir import Capability, CapabilityLevel, SourceRole


class Severity(Enum):
    """Severity level for diagnostic findings."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    def __lt__(self, other: "Severity") -> bool:
        order = [Severity.INFO, Severity.WARNING, Severity.ERROR]
        return order.index(self) < order.index(other)


@dataclass
class Location:
    """Location of a finding in source code."""
    file_path: str
    line_start: int
    line_end: Optional[int] = None
    column_start: Optional[int] = None
    column_end: Optional[int] = None

    def __post_init__(self):
        if self.line_end is None:
            self.line_end = self.line_start

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "column_start": self.column_start,
            "column_end": self.column_end,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Location":
        return cls(
            file_path=d["file_path"],
            line_start=d["line_start"],
            line_end=d.get("line_end"),
            column_start=d.get("column_start"),
            column_end=d.get("column_end"),
        )


@dataclass
class Finding:
    """A diagnostic finding (issue detected by a rule)."""
    rule_id: str
    rule_name: str
    severity: Severity
    location: Location
    message: str
    evidence: List[Location] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "location": self.location.to_dict(),
            "message": self.message,
            "evidence": [e.to_dict() for e in self.evidence],
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Finding":
        return cls(
            rule_id=d["rule_id"],
            rule_name=d["rule_name"],
            severity=Severity(d["severity"]),
            location=Location.from_dict(d["location"]),
            message=d["message"],
            evidence=[Location.from_dict(e) for e in d.get("evidence", [])],
            confidence=d.get("confidence", 1.0),
            metadata=d.get("metadata", {}),
        )


class DiagnosticRule:
    """Base class for diagnostic rules.

    Subclass this to implement a new diagnostic rule:

    ```python
    class MyRule(DiagnosticRule):
        @property
        def rule_id(self) -> str:
            return "my_rule"

        @property
        def rule_name(self) -> str:
            return "My Rule"

        def analyze(self, ir: CodeGraph) -> List[Finding]:
            # Your detection logic here
            pass
    ```
    """

    @property
    def rule_id(self) -> str:
        """Unique identifier for this rule."""
        raise NotImplementedError()

    @property
    def rule_name(self) -> str:
        """Human-readable name for this rule."""
        raise NotImplementedError()

    @property
    def severity(self) -> Severity:
        """Default severity for findings from this rule."""
        return Severity.WARNING

    @property
    def description(self) -> str:
        """Description of what this rule detects."""
        return ""

    @property
    def supported_languages(self) -> Optional[Set[str]]:
        """Languages this rule applies to (None for all)."""
        return None

    @property
    def requires_llm(self) -> bool:
        """Whether this rule requires LLM assistance."""
        return False

    def analyze(self, ir: Any) -> List[Finding]:
        """Analyze the given IR and return findings.

        Args:
            ir: A CodeGraph (from smartbench.graph.schema) to analyze

        Returns:
            List of findings detected by this rule
        """
        raise NotImplementedError()

    @property
    def required_capabilities(self) -> Set[str]:
        """Semantic IR capabilities required by this rule.

        Rules that need richer information can declare requirements here.  The
        engine will report the missing capability instead of silently treating
        unsupported code as clean.
        """
        return set()

    @property
    def analysis_requirements(self) -> Mapping[Capability | str, CapabilityLevel | str]:
        """Minimum semantic strength required by this rule.

        The legacy ``required_capabilities`` set remains supported and means
        full capability.  New rules can request ``PARTIAL`` explicitly when
        their algorithm is conservative by construction.
        """
        return {
            capability: CapabilityLevel.FULL
            for capability in self.required_capabilities
        }

    @property
    def source_roles(self) -> Optional[Set[SourceRole]]:
        """Source provenance roles this rule may make claims about.

        ``None`` preserves the historical all-source behavior.  Rules making
        production security claims should opt into an explicit subset so test
        fixtures and generated examples do not become product-level findings.
        """
        return None

    def _collect_source_files(self, ir: Any) -> List[Tuple[str, str]]:
        """Collect source files from the IR or project path.

        Returns:
            List of (file_path, language) tuples
        """
        graph = getattr(ir, "graph", ir)
        if not hasattr(graph, "nodes"):
            return []

        # First get files from nodes
        seen_files = set()
        files = []

        for node in graph.nodes.values():
            if node.file_path not in seen_files:
                seen_files.add(node.file_path)
                files.append((node.file_path, node.language))

        # If no nodes found, try to scan the project directory
        if not files:
            project_path = getattr(ir, "project_path", "") or graph.meta.get("project_path")
            if project_path:
                root = Path(project_path)
                if root.exists() and root.is_dir():
                    # Extensions to language mapping
                    ext_map = {
                        ".py": "python",
                        ".go": "go",
                        ".java": "java",
                        ".js": "javascript",
                        ".mjs": "javascript",
                        ".ts": "typescript",
                        ".tsx": "typescript",
                        ".rs": "rust",
                    }
                    for ext, lang in ext_map.items():
                        for file_path in root.rglob(f"*{ext}"):
                            try:
                                rel_path = str(file_path.relative_to(root))
                                if rel_path not in seen_files:
                                    seen_files.add(rel_path)
                                    files.append((rel_path, lang))
                            except ValueError:
                                pass

        return files

    def _read_source(self, ir: Any, file_path: str) -> Optional[str]:
        """Read source file given a relative path from the graph."""
        from smartbench.path_safety import read_text_bounded
        try:
            project_path = getattr(ir, "project_path", "")
            if project_path:
                from smartbench.path_safety import resolve_project_file
                resolved = resolve_project_file(Path(project_path).resolve(), file_path)
                if resolved is not None:
                    return read_text_bounded(resolved, 2 * 1024 * 1024)
            graph = getattr(ir, "graph", ir)
            if hasattr(graph, "meta"):
                project_path = graph.meta.get("project_path")
                if project_path:
                    full_path = Path(project_path) / file_path
                    content = read_text_bounded(full_path, 2 * 1024 * 1024)
                    return content
        except Exception:
            pass
        return None


class RuleRegistry:
    """Registry for diagnostic rules.

    Usage:
        registry = RuleRegistry()
        registry.register(NullDereferenceRule())
        registry.register(ResourceLeakRule())

        rules = registry.get_rules_for_language("python")
    """

    def __init__(self):
        self._rules: List[DiagnosticRule] = []
        self._rules_by_id: Dict[str, DiagnosticRule] = {}

    def register(self, rule: DiagnosticRule) -> None:
        """Register a diagnostic rule."""
        if rule.rule_id in self._rules_by_id:
            raise ValueError(f"Rule with id '{rule.rule_id}' already registered")
        self._rules.append(rule)
        self._rules_by_id[rule.rule_id] = rule

    def get_rules_for_language(self, language: str) -> List[DiagnosticRule]:
        """Get all rules applicable to the given language."""
        applicable = []
        for rule in self._rules:
            if rule.supported_languages is None:
                applicable.append(rule)
            elif language in rule.supported_languages:
                applicable.append(rule)
        return applicable

    def get_all_rules(self) -> List[DiagnosticRule]:
        """Get all registered rules."""
        return list(self._rules)

    def get_rule(self, rule_id: str) -> Optional[DiagnosticRule]:
        """Get a rule by its ID."""
        return self._rules_by_id.get(rule_id)

    def list_rule_ids(self) -> List[str]:
        """List all registered rule IDs."""
        return [r.rule_id for r in self._rules]
