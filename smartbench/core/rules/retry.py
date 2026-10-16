"""Diagnostic-rule bridge for counterfactual retry-loop analysis."""

from __future__ import annotations

from typing import List, Optional, Set

from smartbench.analysis.retry_loops import RetryLoopAnalyzer
from smartbench.core.rules.base import (
    AnalysisMethod,
    DiagnosticRule,
    Finding,
    Location,
    Severity,
)
from smartbench.ir import Capability, EvidenceRef, SemanticIR, SourceRole


def _location(reference: EvidenceRef) -> Location:
    return Location(
        file_path=reference.file_path,
        line_start=reference.line_start,
        line_end=reference.line_end,
        column_start=reference.column_start,
        column_end=reference.column_end,
    )


class RetryWithoutBackoffRule(DiagnosticRule):
    """Report error retry loops that have no backoff or bounded exit.

    The structural proof is semantic: an error outcome of an error-checked call
    reaches a loop-back edge without passing a mitigation (a sleep/ticker/
    backoff call, or a bounded-retry guard).  The rule deliberately does not
    claim that the retried operation fails persistently in practice; it exposes
    the shape so a caller can compare the patched behaviour against the status
    quo before proposing the change upstream.
    """

    analysis_method = AnalysisMethod.SEMANTIC

    @property
    def rule_id(self) -> str:
        return "retry_without_backoff"

    @property
    def rule_name(self) -> str:
        return "Retry without backoff"

    @property
    def severity(self) -> Severity:
        return Severity.WARNING

    @property
    def description(self) -> str:
        return (
            "Detects an error path that immediately retries an operation without "
            "backoff or a bounded exit"
        )

    @property
    def required_capabilities(self) -> Set[str]:
        return {Capability.CONTROL_FLOW.value}

    @property
    def supported_languages(self) -> Optional[Set[str]]:
        # Any frontend that lowers loops, branches and calls can use this rule.
        return None

    @property
    def source_roles(self) -> Optional[Set[SourceRole]]:
        return {SourceRole.PRODUCTION}

    def analyze(self, ir: SemanticIR) -> List[Finding]:
        result = RetryLoopAnalyzer().analyze(ir)
        findings: List[Finding] = []
        for violation in result.violations:
            call_name = violation.call.target or violation.call.value or "operation"
            loop_note = ""
            infinite = violation.loop.attributes.get("infinite")
            if infinite is True or str(infinite).lower() == "true":
                loop_note = " in an unbounded loop"
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    location=_location(violation.call.location),
                    message=(
                        f"retry loop calls {call_name} on {violation.error_variable} without "
                        f"backoff or exit{loop_note}; under a persistent failure this spins "
                        f"instead of failing"
                    ),
                    evidence=[
                        _location(reference)
                        for reference in (
                            violation.loop.location,
                            violation.branch.location,
                            violation.loop_back.location,
                        )
                    ],
                    confidence=0.75,
                    metadata={
                        "analysis_method": AnalysisMethod.SEMANTIC.value,
                        "patch_risk": "regression_under_persistent_error",
                        "trigger_operation": call_name,
                        "mitigation": "none",
                        "reachability": "unknown",
                        "assumption": (
                            "the retried operation can fail for a persistent reason"
                        ),
                    },
                )
            )
        return findings
