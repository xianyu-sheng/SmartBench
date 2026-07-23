"""
Base adapter definitions for multi-language parsing.

LanguageAdapter is a wrapper around the existing CodeGraphBuilder
to provide a unified interface for the diagnostic engine.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from smartbench.graph.schema import CodeGraph


class LanguageAdapter(ABC):
    """Base class for language-specific parsers.

    This adapts the existing CodeGraphBuilder to a clean interface
    for the unified diagnostic engine.
    """

    @property
    @abstractmethod
    def language(self) -> str:
        """Return the language name (e.g., "python", "go")."""
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> List[str]:
        """Return the file extensions for this language (e.g., [".py"])."""
        pass

    @abstractmethod
    def can_parse(self, file_path: Path) -> bool:
        """Check if this adapter can parse the given file."""
        pass

    @abstractmethod
    def parse_file(self, file_path: Path, project_root: Path) -> CodeGraph:
        """Parse a single file and return its IR."""
        pass

    @abstractmethod
    def parse_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> CodeGraph:
        """Parse an entire project and return its IR."""
        pass


class AdapterRegistry:
    """Registry for language adapters.

    Usage:
        registry = AdapterRegistry()
        registry.register(PythonAdapter())
        registry.register(GoAdapter())

        adapter = registry.get_adapter_for_file(Path("main.py"))
        graph = adapter.parse_project(project_path)
    """

    def __init__(self):
        self._adapters: List[LanguageAdapter] = []
        self._adapters_by_lang: Dict[str, LanguageAdapter] = {}

    def register(self, adapter: LanguageAdapter) -> None:
        """Register a language adapter."""
        lang = adapter.language
        if lang in self._adapters_by_lang:
            raise ValueError(f"Adapter for language '{lang}' already registered")
        self._adapters.append(adapter)
        self._adapters_by_lang[lang] = adapter

    def get_adapter_for_file(self, file_path: Path) -> Optional[LanguageAdapter]:
        """Get an adapter that can parse the given file."""
        for adapter in self._adapters:
            if adapter.can_parse(file_path):
                return adapter
        return None

    def get_adapter_for_language(self, language: str) -> Optional[LanguageAdapter]:
        """Get an adapter for the given language."""
        return self._adapters_by_lang.get(language)

    def get_all_adapters(self) -> List[LanguageAdapter]:
        """Get all registered adapters."""
        return list(self._adapters)

    def list_languages(self) -> List[str]:
        """List all supported languages."""
        return list(self._adapters_by_lang.keys())
