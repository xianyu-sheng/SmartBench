"""
Python language adapter.

Wraps the existing CodeGraphBuilder to provide Python parsing.
"""

from pathlib import Path
from typing import List, Optional

from smartbench.core.adapters.base import LanguageAdapter
from smartbench.detector.fingerprint import Language
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import CodeGraph


class PythonAdapter(LanguageAdapter):
    """Python language adapter using tree-sitter + regex fallback."""

    @property
    def language(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> List[str]:
        return [".py"]

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.file_extensions

    def parse_file(self, file_path: Path, project_root: Path) -> CodeGraph:
        """Parse a single Python file into CodeGraph."""
        builder = CodeGraphBuilder(
            max_files=1,
            use_treesitter=True,
        )

        # For single file, treat it as the only file in a "project"
        file_dir = file_path.parent
        rel_path = file_path.relative_to(file_dir)

        graph = builder.build(
            str(file_dir),
            Language.PYTHON,
            file_filter=[str(rel_path)],
        )

        # Update meta to point to actual project
        if project_root:
            graph.meta["project_path"] = str(project_root.resolve())

        return graph

    def parse_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> CodeGraph:
        """Parse an entire Python project."""
        builder = CodeGraphBuilder(
            max_files=500,
            use_treesitter=True,
        )

        # Convert Path objects to strings if needed
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
            Language.PYTHON,
            file_filter=str_filter,
        )
