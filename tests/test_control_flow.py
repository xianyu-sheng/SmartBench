"""Control-flow reachability and branch-control contracts."""

from smartbench.analysis import ControlFlowGraph
from smartbench.ir import (
    EvidenceRef,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticOperation,
)


def _operation(operation_id: str, kind: OperationKind) -> SemanticOperation:
    return SemanticOperation(
        id=operation_id,
        kind=kind,
        language="test",
        scope_id="scope",
        location=EvidenceRef("fixture", int(operation_id[1:])),
    )


def test_branch_control_requires_one_reaching_outcome():
    branch = _operation("b1", OperationKind.BRANCH)
    action = _operation("a2", OperationKind.UPDATE)
    exit_operation = _operation("r3", OperationKind.RETURN)
    graph = ControlFlowGraph(
        [branch, action, exit_operation],
        [
            OperationEdge("b1", "a2", OperationEdgeKind.TRUE_BRANCH),
            OperationEdge("b1", "r3", OperationEdgeKind.FALSE_BRANCH),
        ],
    )

    assert graph.reachable("b1", "a2")
    assert graph.dominates("b1", "a2")
    assert graph.branch_controls("b1", "a2")


def test_branch_that_converges_on_action_is_not_a_guard():
    branch = _operation("b1", OperationKind.BRANCH)
    true_call = _operation("c2", OperationKind.CALL)
    false_call = _operation("c3", OperationKind.CALL)
    action = _operation("a4", OperationKind.UPDATE)
    graph = ControlFlowGraph(
        [branch, true_call, false_call, action],
        [
            OperationEdge("b1", "c2", OperationEdgeKind.TRUE_BRANCH),
            OperationEdge("b1", "c3", OperationEdgeKind.FALSE_BRANCH),
            OperationEdge("c2", "a4", OperationEdgeKind.NEXT),
            OperationEdge("c3", "a4", OperationEdgeKind.NEXT),
        ],
    )

    assert graph.reachable("b1", "a4")
    assert not graph.branch_controls("b1", "a4")


def test_loop_back_edges_are_reachable_without_affecting_scope():
    loop = _operation("l1", OperationKind.LOOP)
    action = _operation("a2", OperationKind.UPDATE)
    graph = ControlFlowGraph(
        [loop, action],
        [
            OperationEdge("l1", "a2", OperationEdgeKind.BODY),
            OperationEdge("a2", "l1", OperationEdgeKind.LOOP_BACK),
        ],
    )

    assert graph.reachable("l1", "a2")
    assert graph.reachable("a2", "l1")
    assert not graph.reachable("l1", "missing")
