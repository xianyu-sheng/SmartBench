"""Public entry point for deterministic data-flow analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from smartbench.flow.ast_traversal import AstContext, create_ast_context
from smartbench.flow.findings import (
    FlowFinding,
    create_command_injection_finding,
    create_path_traversal_finding,
    create_sql_injection_finding,
)
from smartbench.flow.taint import TaintTracker
from smartbench.ir import SemanticIR
from smartbench.path_safety import read_text_bounded, resolve_project_file

try:
    from smartbench.graph.tree_parser import get_parser

    HAS_TREE_SITTER = True
except ImportError:  # pragma: no cover - exercised without the graph extra
    HAS_TREE_SITTER = False


@dataclass(frozen=True)
class AnalysisContext:
    """Inputs shared by one file analysis."""

    project_path: str
    language: str
    file_path: str
    source: str


@dataclass(frozen=True)
class AnalysisResult:
    """Result of analyzing one source file."""

    file_path: str
    findings: List[FlowFinding]
    source: str
    success: bool
    error_message: Optional[str] = None


class DataFlowAnalyzer:
    """Perform deterministic, intra-function taint analysis.

    The first implementation deliberately supports Python, JavaScript, and
    TypeScript only. Unsupported languages return an explicit failed result
    instead of silently behaving as if the file were clean.
    """

    _SUPPORTED_LANGUAGES = {"javascript", "python", "typescript"}

    def __init__(self, semantic_ir: Any = None) -> None:
        # ``code_graph`` was the old public parameter name.  Keep the alias
        # while making SemanticIR the preferred analysis boundary.
        self.semantic_ir = semantic_ir
        self.code_graph = semantic_ir
        self.analysis_errors: List[str] = []
        self.files_analyzed = 0

    def analyze_file(
        self,
        file_path: str,
        source: str,
        language: str,
        project_path: str = "",
    ) -> AnalysisResult:
        """Analyze a single file and return evidence-backed findings."""
        normalized_language = self._normalize_language(language)
        if normalized_language not in self._SUPPORTED_LANGUAGES:
            return self._failure(
                file_path,
                source,
                f"Language not supported: {language}",
            )
        if not HAS_TREE_SITTER:
            return self._failure(file_path, source, "Tree-sitter is not available")

        try:
            parser = get_parser(normalized_language)
        except Exception as exc:
            return self._failure(file_path, source, f"Parser unavailable: {exc}")
        if parser is None:
            return self._failure(
                file_path,
                source,
                f"No parser for language: {normalized_language}",
            )

        try:
            context = create_ast_context(file_path, source)
            tree = parser.parse(context.source_bytes)
            tracker = TaintTracker(context)

            from smartbench.flow.taint_simple import SimpleTaintAnalyzer

            analyzer = SimpleTaintAnalyzer(context, tracker, normalized_language)
            raw_findings = analyzer.analyze_and_find_findings(tree.root_node)
            findings = [self._create_finding(raw_finding, context) for raw_finding in raw_findings]
            return AnalysisResult(
                file_path=file_path,
                findings=findings,
                source=source,
                success=True,
            )
        except Exception as exc:
            return self._failure(file_path, source, str(exc))

    def analyze(self, ir: Any = None) -> List[Any]:
        """Analyze all source files referenced by a ``CodeGraph``.

        Findings are converted to the existing rule-system type. File reads
        are constrained to the graph's project root.
        """
        semantic_ir = ir if ir is not None else self.semantic_ir
        graph = getattr(semantic_ir, "graph", semantic_ir)
        if graph is None or not hasattr(graph, "nodes"):
            return []

        project_path = (
            getattr(semantic_ir, "project_path", "")
            or (graph.meta.get("project_path") if hasattr(graph, "meta") else None)
        )
        if not project_path:
            self.analysis_errors.append("SemanticIR has no project_path metadata")
            return []

        root = Path(project_path).resolve()
        files = self._graph_files(semantic_ir)
        base_findings: List[Any] = []

        for file_path in files:
            resolved = resolve_project_file(root, file_path)
            if resolved is None:
                self.analysis_errors.append(f"Skipped unsafe or missing path: {file_path}")
                continue
            source = (
                semantic_ir.read_source(file_path)
                if isinstance(semantic_ir, SemanticIR)
                else read_text_bounded(resolved, 2 * 1024 * 1024)
            )
            if source is None:
                self.analysis_errors.append(f"Unable to read source: {file_path}")
                continue

            language = self._detect_language(file_path)
            if language == "unknown":
                continue
            result = self.analyze_file(file_path, source, language, str(root))
            if not result.success:
                self.analysis_errors.append(
                    f"{file_path}: {result.error_message or 'analysis failed'}"
                )
                continue
            self.files_analyzed += 1
            base_findings.extend(finding.to_base_finding() for finding in result.findings)

        return base_findings

    def _create_finding(
        self,
        raw_finding: dict[str, Any],
        context: AstContext,
    ) -> FlowFinding:
        finding_type = raw_finding["type"]
        arguments = (
            raw_finding["location"],
            raw_finding["snippet"],
            raw_finding["value"],
            context.source,
        )
        if finding_type == "sql_injection":
            finding = create_sql_injection_finding(*arguments)
        elif finding_type == "command_injection":
            finding = create_command_injection_finding(*arguments)
        elif finding_type == "path_traversal":
            finding = create_path_traversal_finding(*arguments)
        else:  # The taint analyzer only emits the three types above.
            raise ValueError(f"Unknown data-flow finding type: {finding_type}")

        confidence = float(raw_finding.get("confidence", finding.confidence))
        reason = str(raw_finding.get("reason", ""))
        finding.confidence = confidence
        finding.metadata["reason"] = reason
        finding.metadata["taint_state"] = raw_finding["value"].taint_state.value
        if confidence < 0.9:
            finding.severity = "warning"
            finding.message = (
                "Dynamic function parameter reaches a dangerous sink; "
                "verify whether callers can supply untrusted data"
            )
        return finding

    def _graph_files(self, semantic_ir: Any) -> List[str]:
        source_units = getattr(semantic_ir, "source_units", {})
        if source_units:
            return [
                path for path in source_units
                if self._detect_language(path) != "unknown"
            ]

        graph = getattr(semantic_ir, "graph", semantic_ir)
        files: dict[str, None] = {}
        for node in graph.nodes.values():
            file_path = getattr(node, "file_path", "")
            if file_path and self._detect_language(file_path) != "unknown":
                files.setdefault(file_path, None)
        return list(files)

    def _failure(self, file_path: str, source: str, message: str) -> AnalysisResult:
        return AnalysisResult(
            file_path=file_path,
            findings=[],
            source=source,
            success=False,
            error_message=message,
        )

    def _normalize_language(self, language: str) -> str:
        aliases = {
            "js": "javascript",
            "jsx": "javascript",
            "py": "python",
            "ts": "typescript",
            "tsx": "typescript",
        }
        normalized = language.lower()
        return aliases.get(normalized, normalized)

    def _detect_language(self, file_path: str) -> str:
        extension = Path(file_path).suffix.lower()
        if extension in {".js", ".jsx", ".mjs", ".cjs"}:
            return "javascript"
        if extension == ".py":
            return "python"
        if extension in {".ts", ".tsx", ".mts", ".cts"}:
            return "typescript"
        return "unknown"
