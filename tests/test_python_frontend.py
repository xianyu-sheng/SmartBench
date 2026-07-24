"""Python frontend lowering into language-neutral semantic operations."""

from pathlib import Path

from smartbench.core.adapters import PythonAdapter
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.ir import Capability, OperationEdgeKind, OperationKind

PYTHON_SOURCE = """
def run(usage, text: str) -> None:
    if usage is not None and usage.finish_reason == "stop":
        return None
    retries += 1
    session.add(retry_message())
    return None
""".strip()


def _lower(tmp_path: Path):
    (tmp_path / "agent.py").write_text(PYTHON_SOURCE, encoding="utf-8")
    return PythonAdapter().parse_semantic_project(tmp_path)


def test_python_frontend_declares_honest_capabilities(tmp_path: Path):
    ir = _lower(tmp_path)
    capabilities = ir.capabilities["python"]

    assert capabilities.supports(Capability.CONTROL_FLOW)
    assert capabilities.is_partial(Capability.DATA_FLOW)
    assert capabilities.is_partial(Capability.EVENT_MODEL)
    assert not capabilities.supports(Capability.TYPE_INFO)


def test_python_frontend_normalizes_common_operations(tmp_path: Path):
    ir = _lower(tmp_path)
    kinds = {operation.kind for operation in ir.operations}

    assert {
        OperationKind.FUNCTION,
        OperationKind.PARAMETER,
        OperationKind.BRANCH,
        OperationKind.RETURN,
        OperationKind.UPDATE,
        OperationKind.CALL,
    }.issubset(kinds)

    branch = next(operation for operation in ir.operations if operation.kind == OperationKind.BRANCH)
    assert "usage.finish_reason" in branch.operands
    assert "'stop'" in branch.attributes["literals"]
    assert "==" in branch.attributes["operators"]

    edge_kinds = {edge.kind for edge in ir.operation_edges}
    assert OperationEdgeKind.NEXT in edge_kinds
    assert OperationEdgeKind.TRUE_BRANCH in edge_kinds
    assert OperationEdgeKind.CONTAINS in edge_kinds


def test_python_lowering_is_deterministic_and_retrievable(tmp_path: Path):
    first = _lower(tmp_path)
    second = PythonAdapter().parse_semantic_project(tmp_path)

    assert [operation.to_dict() for operation in first.operations] == [
        operation.to_dict() for operation in second.operations
    ]

    pack = DeterministicGraphRAG(first).retrieve(
        "finish_reason stop retry",
        max_nodes=8,
    )
    assert any(
        fact.attributes.get("operation_kind") == OperationKind.BRANCH.value
        for fact in pack.facts
    )
    assert any(reference.source == "python_frontend" for reference in pack.evidence)
