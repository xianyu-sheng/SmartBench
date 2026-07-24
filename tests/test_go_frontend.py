"""Go frontend lowering into language-neutral semantic operations."""

from pathlib import Path

import pytest

from smartbench.core.adapters import GoAdapter
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import Capability, OperationEdgeKind, OperationKind, SemanticIR

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


GO_SOURCE = """
package sample

func run(usage *Usage, text string, ch chan string) error {
    if usage != nil && usage.FinishReason == "stop" {
        return nil
    }
    retries++
    session.Add(retryMessage())
    go worker()
    defer cleanup()
    ch <- text
    result := <-ch
    _ = result
    continueWork()
    return nil
}
""".strip()


def _lower(tmp_path: Path) -> SemanticIR:
    (tmp_path / "agent.go").write_text(GO_SOURCE, encoding="utf-8")
    return GoAdapter().parse_semantic_project(tmp_path)


def test_go_frontend_declares_honest_capabilities(tmp_path: Path):
    ir = _lower(tmp_path)
    capabilities = ir.capabilities["go"]

    assert capabilities.supports(Capability.CONTROL_FLOW)
    assert capabilities.is_partial(Capability.DATA_FLOW)
    assert capabilities.is_partial(Capability.CONCURRENCY)
    assert capabilities.is_partial(Capability.EVENT_MODEL)
    assert not capabilities.supports(Capability.TYPE_INFO)


def test_go_frontend_normalizes_control_and_concurrency_operations(tmp_path: Path):
    ir = _lower(tmp_path)
    kinds = {operation.kind for operation in ir.operations}

    assert OperationKind.FUNCTION in kinds
    assert OperationKind.PARAMETER in kinds
    assert OperationKind.BRANCH in kinds
    assert OperationKind.RETURN in kinds
    assert OperationKind.UPDATE in kinds
    assert OperationKind.CALL in kinds
    assert OperationKind.SPAWN in kinds
    assert OperationKind.DEFER in kinds
    assert OperationKind.SEND in kinds
    assert OperationKind.RECEIVE in kinds

    branch = next(
        operation for operation in ir.operations if operation.kind == OperationKind.BRANCH
    )
    assert "FinishReason" in branch.operands
    assert '"stop"' in branch.attributes["literals"]
    assert "==" in branch.attributes["operators"]

    edge_kinds = {edge.kind for edge in ir.operation_edges}
    assert OperationEdgeKind.NEXT in edge_kinds
    assert OperationEdgeKind.TRUE_BRANCH in edge_kinds
    assert OperationEdgeKind.CONTAINS in edge_kinds

    function = next(
        operation for operation in ir.operations if operation.kind == OperationKind.FUNCTION
    )
    parameters = [
        operation for operation in ir.operations if operation.kind == OperationKind.PARAMETER
    ]
    call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "session.Add"
    )
    assert function.attributes["return_types"] == ["error"]
    assert parameters[0].attributes["declared_type"] == "*Usage"
    assert parameters[0].attributes["position"] == 0
    assert call.attributes["arguments"] == ["retryMessage()"]
    assert call.attributes["receiver"] == "session"


def test_go_lowering_is_deterministic_and_graph_rag_can_retrieve_events(tmp_path: Path):
    first = _lower(tmp_path)
    second = GoAdapter().parse_semantic_project(tmp_path)

    assert [operation.to_dict() for operation in first.operations] == [
        operation.to_dict() for operation in second.operations
    ]

    pack = DeterministicGraphRAG(first).retrieve(
        "FinishReason stop return retry",
        max_nodes=8,
    )
    operation_facts = [fact for fact in pack.facts if fact.attributes.get("operation_kind")]
    assert operation_facts
    assert any(
        fact.attributes.get("operation_kind") == OperationKind.BRANCH.value
        for fact in operation_facts
    )
    assert any(ref.source == "go_frontend" for ref in pack.evidence)
