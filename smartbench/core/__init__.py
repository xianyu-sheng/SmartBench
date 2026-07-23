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
    LanguageAdapter,
    PythonAdapter,
)
from smartbench.core.engine import (
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    UnifiedDiagnosticResult,
)
from smartbench.core.rules import (
    DiagnosticRule,
    Finding,
    NullDereferenceRule,
    ResourceLeakRule,
    RuleRegistry,
    Severity,
    register_builtin_rules,
)

__all__ = [
    # Rules
    "DiagnosticRule",
    "Finding",
    "RuleRegistry",
    "Severity",
    "NullDereferenceRule",
    "ResourceLeakRule",
    "register_builtin_rules",
    # Engine
    "UnifiedDiagnosticConfig",
    "UnifiedDiagnosticEngine",
    "UnifiedDiagnosticResult",
    # Adapters
    "AdapterRegistry",
    "LanguageAdapter",
    "PythonAdapter",
    "GoAdapter",
]
