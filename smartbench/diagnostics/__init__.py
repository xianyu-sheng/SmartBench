"""Pluggable diagnostic tool registry — language-agnostic diagnostics."""

from smartbench.diagnostics.executor import run_tools_for_strategy
from smartbench.diagnostics.registry import (
    DiagnosisResult,
    DiagnosticRegistry,
    DiagnosticTool,
    ProblemCategory,
    Severity,
)
from smartbench.diagnostics.tools import ALL_TOOLS

__all__ = ["DiagnosticRegistry", "DiagnosticTool", "DiagnosisResult",
           "ProblemCategory", "Severity", "ALL_TOOLS",
           "run_tools_for_strategy"]
