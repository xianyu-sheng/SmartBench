"""Pluggable diagnostic tool registry — language-agnostic diagnostics."""

from smartbench.diagnostics.registry import (
    DiagnosticRegistry, DiagnosticTool, DiagnosisResult,
    ProblemCategory, Severity,
)
from smartbench.diagnostics.tools import ALL_TOOLS
from smartbench.diagnostics.executor import run_tools_for_strategy

__all__ = ["DiagnosticRegistry", "DiagnosticTool", "DiagnosisResult",
           "ProblemCategory", "Severity", "ALL_TOOLS",
           "run_tools_for_strategy"]
