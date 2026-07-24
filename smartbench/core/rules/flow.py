"""Integration between deterministic data flow and the rule engine."""

from typing import List, Set

from smartbench.core.rules.base import DiagnosticRule, Finding, Severity
from smartbench.graph.schema import CodeGraph


class DataFlowSecurityRule(DiagnosticRule):
    """Run the AST-based security data-flow suite once per graph."""

    @property
    def rule_id(self) -> str:
        return "security_data_flow"

    @property
    def rule_name(self) -> str:
        return "Security Data Flow"

    @property
    def severity(self) -> Severity:
        return Severity.ERROR

    @property
    def description(self) -> str:
        return (
            "Tracks known inputs and function parameters into SQL, command, "
            "and filesystem sinks using tree-sitter ASTs"
        )

    @property
    def supported_languages(self) -> Set[str]:
        return {"javascript", "python", "typescript"}

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        from smartbench.flow import DataFlowAnalyzer

        analyzer = DataFlowAnalyzer(ir)
        findings = analyzer.analyze()
        if analyzer.analysis_errors and analyzer.files_analyzed == 0:
            details = "; ".join(analyzer.analysis_errors[:3])
            raise RuntimeError(f"Data-flow analysis could not process any files: {details}")
        return findings


# Backward-compatible class names for code that imported the prototype rules.
DataFlowSqlInjectionRule = DataFlowSecurityRule
DataFlowCommandInjectionRule = DataFlowSecurityRule
DataFlowPathTraversalRule = DataFlowSecurityRule


def register_flow_rules(registry) -> None:
    """Register the composite data-flow rule."""
    registry.register(DataFlowSecurityRule())
