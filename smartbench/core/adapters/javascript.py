"""
JavaScript and TypeScript language adapters.

Wraps the existing CodeGraphBuilder to provide parsing.
"""

from pathlib import Path
from typing import List, Optional

from smartbench.core.adapters.base import LanguageAdapter
from smartbench.detector.fingerprint import Language as DetectorLanguage
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import CodeGraph


class JavaScriptAdapter(LanguageAdapter):
    """JavaScript language adapter using tree-sitter + regex fallback."""

    @property
    def language(self) -> str:
        return "javascript"

    @property
    def file_extensions(self) -> List[str]:
        return [".js", ".jsx", ".mjs", ".cjs"]

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.file_extensions

    def parse_file(self, file_path: Path, project_root: Path) -> CodeGraph:
        """Parse a single JavaScript file into CodeGraph."""
        builder = CodeGraphBuilder(
            max_files=1,
            use_treesitter=True,
        )

        file_dir = file_path.parent
        try:
            rel_path = file_path.relative_to(file_dir)
        except ValueError:
            rel_path = file_path

        graph = builder.build(
            str(file_dir),
            DetectorLanguage.JAVASCRIPT,
            file_filter=[str(rel_path)],
        )

        if project_root:
            graph.meta["project_path"] = str(project_root.resolve())

        return graph

    def parse_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> CodeGraph:
        """Parse an entire JavaScript project."""
        builder = CodeGraphBuilder(
            max_files=500,
            use_treesitter=True,
        )

        str_filter = None
        if file_paths:
            root = project_path.resolve()
            str_filter = []
            for fp in file_paths:
                try:
                    str_filter.append(str(fp.resolve().relative_to(root)))
                except ValueError:
                    str_filter.append(str(fp))

        return builder.build(
            str(project_path),
            DetectorLanguage.JAVASCRIPT,
            file_filter=str_filter,
        )


class TypeScriptAdapter(LanguageAdapter):
    """TypeScript language adapter using tree-sitter + regex fallback."""

    @property
    def language(self) -> str:
        return "typescript"

    @property
    def file_extensions(self) -> List[str]:
        return [".ts", ".tsx", ".mts", ".cts"]

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.file_extensions

    def parse_file(self, file_path: Path, project_root: Path) -> CodeGraph:
        """Parse a single TypeScript file into CodeGraph."""
        builder = CodeGraphBuilder(
            max_files=1,
            use_treesitter=True,
        )

        file_dir = file_path.parent
        try:
            rel_path = file_path.relative_to(file_dir)
        except ValueError:
            rel_path = file_path

        graph = builder.build(
            str(file_dir),
            DetectorLanguage.TYPESCRIPT,
            file_filter=[str(rel_path)],
        )

        if project_root:
            graph.meta["project_path"] = str(project_root.resolve())

        return graph

    def parse_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> CodeGraph:
        """Parse an entire TypeScript project."""
        builder = CodeGraphBuilder(
            max_files=500,
            use_treesitter=True,
        )

        str_filter = None
        if file_paths:
            root = project_path.resolve()
            str_filter = []
            for fp in file_paths:
                try:
                    str_filter.append(str(fp.resolve().relative_to(root)))
                except ValueError:
                    str_filter.append(str(fp))

        return builder.build(
            str(project_path),
            DetectorLanguage.TYPESCRIPT,
            file_filter=str_filter,
        )
