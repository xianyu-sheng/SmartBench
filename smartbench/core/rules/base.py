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
from typing import Any, Dict, List, Optional, Set


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
