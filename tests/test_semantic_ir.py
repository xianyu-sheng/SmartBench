"""Contracts for the language-neutral SemanticIR boundary."""

from pathlib import Path

from smartbench.core.adapters import PythonAdapter
from smartbench.graph.schema import CodeGraph, CodeNode, NodeType
from smartbench.ir import Capability, CapabilitySet, SemanticIR


def test_frontend_lowers_legacy_graph_to_semantic_ir(tmp_path: Path):
    source_file = tmp_path / "main.py"
    source_file.write_text("def hello():\n    return 1\n", encoding="utf-8")

    semantic_ir = PythonAdapter().parse_semantic_project(tmp_path)

    assert isinstance(semantic_ir, SemanticIR)
    assert semantic_ir.schema_version == "semantic-ir/v1"
    assert semantic_ir.project_path == str(tmp_path.resolve())
    assert "python" in semantic_ir.languages
    assert semantic_ir.supports(Capability.STRUCTURE, "python")
    assert "main.py" in semantic_ir.source_units
    assert semantic_ir.read_source("main.py") == source_file.read_text(encoding="utf-8")


def test_capability_set_distinguishes_missing_and_partial():
    capabilities = CapabilitySet.from_values(
        "go",
        [Capability.STRUCTURE],
        partial={Capability.CALL_GRAPH: "heuristic resolution"},
    )

    assert capabilities.supports(Capability.STRUCTURE)
    assert not capabilities.supports(Capability.CALL_GRAPH)
    assert capabilities.is_partial(Capability.CALL_GRAPH)
    assert capabilities.missing([Capability.STRUCTURE, Capability.DATA_FLOW]) == ["data_flow"]


def test_semantic_ir_keeps_graph_compatibility_and_merges_capabilities(tmp_path: Path):
    graph = CodeGraph(meta={"project_path": str(tmp_path)})
    graph.add_node(
        CodeNode(
            id="n1",
            node_type=NodeType.FUNCTION,
            name="f",
            file_path="f.py",
            line_start=1,
            line_end=1,
            language="python",
        )
    )
    ir = SemanticIR.from_graph(graph, language="python", project_path=str(tmp_path))
    other = SemanticIR.from_graph(graph, language="go", project_path=str(tmp_path))
    merged = ir.merge(other)

    assert ir.nodes is graph.nodes
    assert set(merged.nodes) == {"n1"}
    assert set(merged.languages) == {"go", "python"}
    assert merged.meta["semantic_ir_version"] == "semantic-ir/v1"
