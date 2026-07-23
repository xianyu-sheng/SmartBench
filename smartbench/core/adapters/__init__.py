"""
Language Adapters - Parse source code into unified CodeGraph IR.

This module provides the adapter abstraction for multi-language support:
  - LanguageAdapter: Base class for language-specific parsers
  - AdapterRegistry: Register and retrieve adapters by language
  - Built-in adapters for Python, Go, JavaScript, TypeScript, Rust
"""

from smartbench.core.adapters.base import (
    AdapterRegistry,
    LanguageAdapter,
)
from smartbench.core.adapters.go import GoAdapter
from smartbench.core.adapters.python import PythonAdapter

__all__ = [
    "AdapterRegistry",
    "LanguageAdapter",
    "PythonAdapter",
    "GoAdapter",
]
