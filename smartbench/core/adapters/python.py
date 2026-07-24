"""
Python language adapter.

Wraps the existing CodeGraphBuilder to provide Python parsing.
"""

from pathlib import Path
from typing import List, Optional

from smartbench.core.adapters.base import LanguageAdapter
from smartbench.detector.fingerprint import Language
from smartbench.frontends.python import PythonSemanticLowerer
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import CodeGraph
from smartbench.ir import Capability, CapabilitySet, SemanticIR, validate_semantic_ir


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

    @property
    def semantic_capabilities(self) -> CapabilitySet:
        return CapabilitySet.from_values(
            self.language,
            [
                Capability.STRUCTURE,
                Capability.SOURCE_LOCATIONS,
                Capability.SYMBOLS,
                Capability.CONTROL_FLOW,
            ],
            partial={
                Capability.CALL_GRAPH: "structural resolution does not include runtime dispatch",
                Capability.DATA_FLOW: "operation operands plus conservative argument/return propagation; "
                "dynamic dispatch is unresolved",
                Capability.EVENT_MODEL: "branch and transition operations are intraprocedural",
                Capability.TYPE_INFO: "annotations and constructor syntax are preserved; no type checker",
            },
        )

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
        lowered = PythonSemanticLowerer().lower(ir)
        ir.operations.extend(lowered.operations)
        ir.operation_edges.extend(lowered.edges)
        ir.facts.extend(lowered.facts)
        contract_errors = validate_semantic_ir(lowered.operations)
        ir.meta["python_frontend"] = {
            "files_analyzed": lowered.files_analyzed,
            "operations": len(lowered.operations),
            "operation_edges": len(lowered.edges),
            "errors": [*lowered.errors, *contract_errors],
        }
        ir.meta["semantic_contract"] = {
            "version": "semantic-ir/contracts/v1",
            "valid": not contract_errors,
            "errors": list(contract_errors),
        }
        return ir
