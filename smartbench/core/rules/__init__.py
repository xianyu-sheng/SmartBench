"""
Diagnostic Rules - Language-agnostic detection patterns.

This module defines the rule abstraction and provides built-in rules:
  - Base classes: DiagnosticRule, Finding
  - Rule registry for cross-language detection
  - Built-in rules like null dereference, resource leak, etc.
"""

from smartbench.core.rules.base import (
    DiagnosticRule,
    Finding,
    RuleRegistry,
    Severity,
)
from smartbench.core.rules.common import (
    NullDereferenceRule,
    ResourceLeakRule,
    register_builtin_rules,
)

__all__ = [
    "DiagnosticRule",
    "Finding",
    "RuleRegistry",
    "Severity",
    "NullDereferenceRule",
    "ResourceLeakRule",
    "register_builtin_rules",
]
