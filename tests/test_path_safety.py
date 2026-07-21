"""Repository-boundary tests for scanners, retrieval, and verification."""

from pathlib import Path

from smartbench.detector.fingerprint import Language
from smartbench.detector.scanner import ProjectScanner
from smartbench.diagnostics.registry import ProblemCategory
from smartbench.diagnostics.tools import PythonDiagTool
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.retriever import GraphRetriever
from smartbench.graph.schema import CodeGraph
from smartbench.path_safety import is_project_file, resolve_project_file
from smartbench.rag.chunker import CodeChunker
from smartbench.rag.retriever import HybridRetriever
from smartbench.verifier.extractor import EvidenceExtractor
from smartbench.verifier.location import LocationVerifier


def _project_with_external_symlink(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def local():\n    return 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("def secret():\n    return 'outside'\n")
    (project / "leak.py").symlink_to(outside)
    return project, outside


def test_path_helpers_reject_escape_absolute_and_external_symlink(tmp_path):
    project, outside = _project_with_external_symlink(tmp_path)

    assert resolve_project_file(project, "main.py") == project / "main.py"
    assert resolve_project_file(project, "../outside.py") is None
    assert resolve_project_file(project, outside) is None
    assert resolve_project_file(project, "leak.py") is None
    assert is_project_file(project, project / "main.py") is True
    assert is_project_file(project, project / "leak.py") is False


def test_scanner_graph_and_chunker_skip_external_symlink(tmp_path):
    project, _ = _project_with_external_symlink(tmp_path)

    fingerprint = ProjectScanner(str(project)).scan()
    graph = CodeGraphBuilder(use_treesitter=False).build(
        str(project), Language.PYTHON
    )
    chunks = CodeChunker().chunk_project(str(project), graph)

    assert fingerprint.source_files == 1
    assert all(node.file_path != "leak.py" for node in graph.nodes.values())
    assert all(chunk.file_path != "leak.py" for chunk in chunks)


def test_retrievers_and_verifiers_cannot_read_outside_project(tmp_path):
    project, _ = _project_with_external_symlink(tmp_path)
    graph = CodeGraph(meta={"project_path": str(project)})

    graph_retriever = GraphRetriever(graph, str(project))
    hybrid = HybridRetriever(graph, str(project))
    location = LocationVerifier(str(project))
    extractor = EvidenceExtractor(str(project), graph)

    for unsafe in ("../outside.py", "leak.py"):
        assert "Could not read" in graph_retriever.retrieve_by_file(unsafe)
        assert hybrid.verify_location(unsafe)["exists"] is False
        assert extractor.extract_at(unsafe) is None
        assert location.verify(unsafe).resolved_file is None


def test_python_syntax_probe_skips_external_symlink(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def local():\n    return 1\n")
    outside = tmp_path / "broken.py"
    outside.write_text("def broken(:\n    pass\n")
    (project / "linked.py").symlink_to(outside)

    result = PythonDiagTool().diagnose(
        str(project), ProblemCategory.STARTUP_FAILURE
    )

    assert result.symptoms == []
    assert "Parsed 1 Python files" in result.evidence
