"""Diagnostic-rule bridge for declarative state-machine invariants."""

from __future__ import annotations

from typing import Optional, Set

from smartbench.analysis import StateMachineAnalyzer
from smartbench.analysis.spec import StateRuleDefinition
from smartbench.core.rules.base import AnalysisMethod, DiagnosticRule, Finding, Location, Severity
from smartbench.ir import Capability, SemanticIR


class DeclarativeStateRule(DiagnosticRule):
    """Expose one validated state invariant through the common rule API."""

    analysis_method = AnalysisMethod.SEMANTIC

    def __init__(self, definition: StateRuleDefinition):
        self.definition = definition

    @property
    def rule_id(self) -> str:
        return self.definition.rule_id

    @property
    def rule_name(self) -> str:
        return self.definition.name

    @property
    def severity(self) -> Severity:
        return Severity(self.definition.severity)

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def supported_languages(self) -> Optional[Set[str]]:
        return set(self.definition.languages) or None

    @property
    def required_capabilities(self) -> Set[str]:
        return {Capability.CONTROL_FLOW.value}

    def analyze(self, ir: SemanticIR) -> list[Finding]:
        result = StateMachineAnalyzer().analyze(
            ir,
            [self.definition.invariant],
            languages=self.definition.languages or None,
        )
        findings: list[Finding] = []
        for violation in result.violations:
            action = violation.action.location
            event = violation.event.location
            evidence = [event, action]
            seen = {
                (reference.file_path, reference.line_start, reference.line_end)
                for reference in evidence
            }
            for operation in violation.path_operations:
                reference = operation.location
                key = (reference.file_path, reference.line_start, reference.line_end)
                if key not in seen:
                    evidence.append(reference)
                    seen.add(key)
                if len(evidence) >= 8:
                    break
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    location=_location(action),
                    message=violation.message,
                    evidence=[_location(reference) for reference in evidence],
                    confidence=self.definition.confidence,
                    metadata={
                        "analysis_kind": "state_machine",
                        "invariant_id": violation.invariant_id,
                        "scope_id": violation.scope_id,
                        "event_operation": violation.event.id,
                        "action_operation": violation.action.id,
                        "missing": violation.missing,
                        "scope": self.definition.invariant.scope.value,
                        "max_call_depth": self.definition.invariant.max_call_depth,
                        "path_operations": [
                            operation.id for operation in violation.path_operations
                        ],
                        "path_arcs": (
                            [kind.value for kind in violation.path.arc_kinds]
                            if violation.path is not None
                            else []
                        ),
                    },
                )
            )
        return findings


def _location(reference) -> Location:
    return Location(
        file_path=reference.file_path,
        line_start=reference.line_start,
        line_end=reference.line_end,
        column_start=reference.column_start,
        column_end=reference.column_end,
    )
