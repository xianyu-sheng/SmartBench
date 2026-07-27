"""
JavaScript and TypeScript language adapters.

Wraps the existing CodeGraphBuilder to provide parsing.
"""

from pathlib import Path
from typing import List, Optional

from smartbench.core.adapters.base import LanguageAdapter
from smartbench.detector.fingerprint import Language as DetectorLanguage
from smartbench.frontends.javascript import JavaScriptSemanticLowerer
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import CodeGraph
from smartbench.ir import Capability, CapabilitySet, SemanticIR, validate_semantic_ir


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

    @property
    def semantic_capabilities(self) -> CapabilitySet:
        return CapabilitySet.from_values(
            self.language,
            [Capability.STRUCTURE, Capability.SOURCE_LOCATIONS, Capability.SYMBOLS],
            partial={
                Capability.CALL_GRAPH: "derived from the structural graph; dynamic dispatch is unresolved",
                Capability.DATA_FLOW: "intra-procedural AST taint analysis; interprocedural flow is unresolved",
                Capability.CONTROL_FLOW: "branches and loops are normalized; exceptions and async scheduling are unresolved",
                Capability.EVENT_MODEL: "statement-level events are normalized; framework lifecycle is unresolved",
            },
        )

    def parse_semantic_file(self, file_path: Path, project_root: Path) -> SemanticIR:
        return self._lower_semantics(super().parse_semantic_file(file_path, project_root))

    def parse_semantic_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> SemanticIR:
        return self._lower_semantics(
            super().parse_semantic_project(project_path, file_paths=file_paths)
        )

    @staticmethod
    def _lower_semantics(ir: SemanticIR) -> SemanticIR:
        lowered = JavaScriptSemanticLowerer().lower(ir)
        ir.operations.extend(lowered.operations)
        ir.operation_edges.extend(lowered.edges)
        ir.facts.extend(lowered.facts)
        contract_errors = validate_semantic_ir(lowered.operations)
        ir.meta["javascript_frontend"] = {
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
            max_files=max(1, len(file_paths)) if file_paths is not None else 500,
            use_treesitter=True,
        )

        str_filter = None
        if file_paths is not None:
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

    @property
    def semantic_capabilities(self) -> CapabilitySet:
        return CapabilitySet.from_values(
            self.language,
            [Capability.STRUCTURE, Capability.SOURCE_LOCATIONS, Capability.SYMBOLS],
            partial={
                Capability.CALL_GRAPH: "derived from the structural graph; dynamic dispatch is unresolved",
                Capability.DATA_FLOW: "intra-procedural AST taint analysis; type-aware flow is unresolved",
                Capability.CONTROL_FLOW: "branches and loops are normalized; exceptions and async scheduling are unresolved",
                Capability.EVENT_MODEL: "statement-level events are normalized; framework lifecycle is unresolved",
                Capability.TYPE_INFO: "surface annotations are preserved; no TypeScript type checker is invoked",
            },
        )

    def parse_semantic_file(self, file_path: Path, project_root: Path) -> SemanticIR:
        return JavaScriptAdapter._lower_semantics(
            super().parse_semantic_file(file_path, project_root)
        )

    def parse_semantic_project(
        self, project_path: Path, file_paths: Optional[List[Path]] = None
    ) -> SemanticIR:
        return JavaScriptAdapter._lower_semantics(
            super().parse_semantic_project(project_path, file_paths=file_paths)
        )

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
            max_files=max(1, len(file_paths)) if file_paths is not None else 500,
            use_treesitter=True,
        )

        str_filter = None
        if file_paths is not None:
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
