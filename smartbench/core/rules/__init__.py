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
from smartbench.core.rules.quality import (
    ExceptionTooBroadRule,
    InsecureRandomRule,
    SqlInjectionRule,
    TodoFixmeRule,
    UnusedImportRule,
    register_quality_rules,
)
from smartbench.core.rules.security import (
    CommandInjectionRule,
    HardcodedSecretRule,
    PathTraversalRule,
    register_security_rules,
)
from smartbench.core.rules.state_machine import DeclarativeStateRule

__all__ = [
    "DiagnosticRule",
    "Finding",
    "RuleRegistry",
    "Severity",
    # Common rules
    "NullDereferenceRule",
    "ResourceLeakRule",
    "register_builtin_rules",
    # Security rules
    "CommandInjectionRule",
    "HardcodedSecretRule",
    "PathTraversalRule",
    "register_security_rules",
    # Quality rules
    "ExceptionTooBroadRule",
    "InsecureRandomRule",
    "SqlInjectionRule",
    "TodoFixmeRule",
    "UnusedImportRule",
    "register_quality_rules",
    "DeclarativeStateRule",
]
