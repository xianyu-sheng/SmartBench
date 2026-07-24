"""
Go language adapter.

Wraps the existing CodeGraphBuilder to provide Go parsing.
"""

from pathlib import Path
from typing import List, Optional

from smartbench.core.adapters.base import LanguageAdapter
from smartbench.detector.fingerprint import Language
from smartbench.frontends.go import GoSemanticLowerer
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import CodeGraph
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import Capability, CapabilitySet, SemanticIR


class GoAdapter(LanguageAdapter):
    """Go language adapter using tree-sitter + regex fallback."""

    @property
    def language(self) -> str:
        return "go"

    @property
    def file_extensions(self) -> List[str]:
        return [".go"]

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.file_extensions

    @property
    def semantic_capabilities(self) -> CapabilitySet:
        if get_parser("go") is None:
            return super().semantic_capabilities
        return CapabilitySet.from_values(
            self.language,
            [
                Capability.STRUCTURE,
                Capability.SOURCE_LOCATIONS,
                Capability.SYMBOLS,
                Capability.CONTROL_FLOW,
            ],
            partial={
                Capability.CALL_GRAPH: "surface types resolve proven receivers; no go/types dispatch",
                Capability.DATA_FLOW: "operation operands plus conservative argument/return propagation",
                Capability.CONCURRENCY: "goroutine, defer, send, receive and select operations are normalized",
                Capability.EVENT_MODEL: "branch and transition operations are intraprocedural",
                Capability.TYPE_INFO: "surface parameter, receiver and return types only; no go/types",
            },
        )

    def parse_file(self, file_path: Path, project_root: Path) -> CodeGraph:
        """Parse a single Go file into CodeGraph."""
        builder = CodeGraphBuilder(
            max_files=1,
            use_treesitter=True,
        )

        file_dir = file_path.parent
        rel_path = file_path.relative_to(file_dir)

        graph = builder.build(
            str(file_dir),
            Language.GO,
            file_filter=[str(rel_path)],
        )

        if project_root:
            graph.meta["project_path"] = str(project_root.resolve())

        return graph

    def parse_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> CodeGraph:
        """Parse an entire Go project."""
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
            Language.GO,
            file_filter=str_filter,
        )

    def parse_semantic_file(self, file_path: Path, project_root: Path) -> SemanticIR:
        ir = super().parse_semantic_file(file_path, project_root)
        return self._lower_semantics(ir)

    def parse_semantic_project(
        self,
        project_path: Path,
        file_paths: Optional[List[Path]] = None,
    ) -> SemanticIR:
        ir = super().parse_semantic_project(project_path, file_paths=file_paths)
        return self._lower_semantics(ir)

    def _lower_semantics(self, ir: SemanticIR) -> SemanticIR:
        lowered = GoSemanticLowerer().lower(ir)
        ir.operations.extend(lowered.operations)
        ir.operation_edges.extend(lowered.edges)
        ir.facts.extend(lowered.facts)
        ir.meta["go_frontend"] = {
            "files_analyzed": lowered.files_analyzed,
            "operations": len(lowered.operations),
            "operation_edges": len(lowered.edges),
            "errors": list(lowered.errors),
        }
        return ir
