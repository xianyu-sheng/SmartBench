"""
Language Adapters - Parse source code into unified CodeGraph IR.

This module provides the adapter abstraction for multi-language support:
  - LanguageAdapter: Base class for language-specific parsers
  - AdapterRegistry: Register and retrieve adapters by language
  - Built-in adapters for Python, Go, JavaScript, TypeScript, Rust, Java
"""

from smartbench.core.adapters.base import (
    AdapterRegistry,
    FrontendRegistry,
    LanguageAdapter,
    LanguageFrontend,
)
from smartbench.core.adapters.go import GoAdapter
from smartbench.core.adapters.java import JavaAdapter
from smartbench.core.adapters.javascript import JavaScriptAdapter, TypeScriptAdapter
from smartbench.core.adapters.python import PythonAdapter
from smartbench.core.adapters.rust import RustAdapter


def register_all_adapters(registry: AdapterRegistry) -> None:
    """Register all built-in language adapters."""
    registry.register(PythonAdapter())
    registry.register(GoAdapter())
    registry.register(JavaAdapter())
    registry.register(JavaScriptAdapter())
    registry.register(TypeScriptAdapter())
    registry.register(RustAdapter())


__all__ = [
    "AdapterRegistry",
    "FrontendRegistry",
    "LanguageAdapter",
    "LanguageFrontend",
    "PythonAdapter",
    "GoAdapter",
    "JavaAdapter",
    "JavaScriptAdapter",
    "TypeScriptAdapter",
    "RustAdapter",
    "register_all_adapters",
]
