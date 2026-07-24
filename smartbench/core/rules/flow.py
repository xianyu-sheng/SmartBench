"""Integration between deterministic data flow and the rule engine."""

from typing import List, Mapping, Set

from smartbench.core.rules.base import DiagnosticRule, Finding, Severity
from smartbench.graph.schema import CodeGraph
from smartbench.ir import Capability, CapabilityLevel, SourceRole


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

    @property
    def analysis_requirements(self) -> Mapping[Capability | str, CapabilityLevel | str]:
        # The taint engine is deliberately intra-procedural and conservative;
        # claiming FULL data-flow here would make its limitations invisible.
        return {Capability.DATA_FLOW: CapabilityLevel.PARTIAL}

    @property
    def source_roles(self) -> Set[SourceRole]:
        # Test/evaluation fixtures are useful inputs for the analyzer itself,
        # but findings there are not production bug claims.
        return {SourceRole.PRODUCTION, SourceRole.UNKNOWN}

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
