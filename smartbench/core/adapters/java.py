"""
Java language adapter.

Wraps the existing CodeGraphBuilder to provide Java parsing.
"""

from pathlib import Path
from typing import List, Optional

from smartbench.core.adapters.base import LanguageAdapter
from smartbench.detector.fingerprint import Language as DetectorLanguage
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import CodeGraph


class JavaAdapter(LanguageAdapter):
    """Java language adapter using tree-sitter + regex fallback."""

    @property
    def language(self) -> str:
        return "java"

    @property
    def file_extensions(self) -> List[str]:
        return [".java", ".jav"]

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.file_extensions

    def parse_file(self, file_path: Path, project_root: Path) -> CodeGraph:
        """Parse a single Java file into CodeGraph."""
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
            DetectorLanguage.JAVA,
            file_filter=[str(rel_path)],
        )

        # Update meta to point to actual project
        if project_root:
            graph.meta["project_path"] = str(project_root.resolve())

        return graph

    def parse_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> CodeGraph:
        """Parse an entire Java project."""
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
            DetectorLanguage.JAVA,
            file_filter=str_filter,
        )
