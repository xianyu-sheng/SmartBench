"""
SmartBench Core - Unified Diagnostic Framework.

This module provides the multi-language unified diagnostic infrastructure:
  - Rules: Language-agnostic diagnostic rules
  - Engine: Rule execution and finding aggregation
  - Adapters: Language-specific parsing to unified IR

The existing `smartbench.graph` module is our IR foundation.
"""

from smartbench.core.adapters import (
    AdapterRegistry,
    GoAdapter,
    JavaAdapter,
    JavaScriptAdapter,
    LanguageAdapter,
    PythonAdapter,
    RustAdapter,
    TypeScriptAdapter,
    register_all_adapters,
)
from smartbench.core.engine import (
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    UnifiedDiagnosticResult,
)
from smartbench.core.rules import (
    CommandInjectionRule,
    DiagnosticRule,
    ExceptionTooBroadRule,
    Finding,
    HardcodedSecretRule,
    InsecureRandomRule,
    NullDereferenceRule,
    PathTraversalRule,
    ResourceLeakRule,
    RuleRegistry,
    Severity,
    SqlInjectionRule,
    TodoFixmeRule,
    UnusedImportRule,
    register_builtin_rules,
    register_quality_rules,
    register_security_rules,
)

__all__ = [
    # Rules
    "DiagnosticRule",
    "Finding",
    "RuleRegistry",
    "Severity",
    "register_builtin_rules",
    "register_quality_rules",
    "register_security_rules",
    # Common rules
    "NullDereferenceRule",
    "ResourceLeakRule",
    # Security rules
    "CommandInjectionRule",
    "HardcodedSecretRule",
    "PathTraversalRule",
    "SqlInjectionRule",
    # Quality rules
    "ExceptionTooBroadRule",
    "InsecureRandomRule",
    "TodoFixmeRule",
    "UnusedImportRule",
    # Engine
    "UnifiedDiagnosticConfig",
    "UnifiedDiagnosticEngine",
    "UnifiedDiagnosticResult",
    # Adapters
    "AdapterRegistry",
    "LanguageAdapter",
    "PythonAdapter",
    "GoAdapter",
    "JavaAdapter",
    "JavaScriptAdapter",
    "TypeScriptAdapter",
    "RustAdapter",
    "register_all_adapters",
]
