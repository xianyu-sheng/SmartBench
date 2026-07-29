"""Typed SemanticIR contract and bounded ICFG regression tests."""

from pathlib import Path

import pytest

from smartbench.analysis import (
    ICFGArcKind,
    InterproceduralControlFlowGraph,
    InterproceduralStatePathQuery,
    OperationSelector,
    SemanticLinker,
)
from smartbench.core.adapters import GoAdapter, PythonAdapter
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    CallContract,
    FunctionContract,
    OperationKind,
    ParameterContract,
    validate_semantic_ir,
)


def test_python_frontend_emits_valid_typed_contract_and_icfg(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        """
def middle(value: int) -> int:
    return value

def top(value: int) -> int:
    result = middle(value)
    emit(result)
    return result
""".strip(),
        encoding="utf-8",
    )
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    link = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, link)

    assert validate_semantic_ir(ir.operations) == ()
    assert ir.meta["semantic_contract"]["valid"] is True

    functions = {
        operation.target: operation
        for operation in ir.operations
        if operation.kind == OperationKind.FUNCTION
    }
    middle = FunctionContract.from_operation(functions["middle"])
    assert middle.return_types == ("int",)
    parameter = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.PARAMETER
        and operation.scope_id == functions["middle"].id
    )
    assert ParameterContract.from_operation(parameter).declared_type == "int"

    middle_call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "middle"
    )
    call = CallContract.from_operation(middle_call)
    assert call.arguments == ("value",)
    assert call.result_targets == ("result",)
    assert call.host_operation

    emit = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "emit"
    )
    icfg = InterproceduralControlFlowGraph(ir)
    path = icfg.path(functions["top"].id, emit.id, max_call_depth=2)
    assert path is not None
    assert ICFGArcKind.CALL_ENTER in path.arc_kinds
    assert ICFGArcKind.CALL_RETURN in path.arc_kinds
    assert icfg.calls_to(functions["middle"].id) == (middle_call.id,)
    assert icfg.path(functions["top"].id, emit.id, max_call_depth=0) is None


def test_cross_function_state_path_is_evidence_backed(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        """
def worker():
    emit("worker")

def run():
    marker = 1
    worker()
""".strip(),
        encoding="utf-8",
    )
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    link = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, link)

    paths = InterproceduralStatePathQuery().find(
        ir,
        OperationSelector.of(OperationKind.ASSIGN, contains_all=("marker",)),
        OperationSelector.of(OperationKind.CALL, contains_all=("emit",)),
        max_call_depth=2,
    )
    assert len(paths) == 1
    path = paths[0]
    assert path.event.scope_id != path.action.scope_id
    assert path.path.call_depth == 1
    fact = path.to_fact()
    assert fact.attributes["link_kind"] == "interprocedural_state_path"
    assert fact.attributes["path_arcs"] == [
        "control_flow",
        "call_enter",
        "function_entry",
    ]
    assert len(fact.evidence) >= 2


def test_contract_validator_rejects_misaligned_call_metadata(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "def run():\n    return helper(1)\n\ndef helper(value):\n    return value\n",
        encoding="utf-8",
    )
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    call = next(operation for operation in ir.operations if operation.kind == OperationKind.CALL)
    attributes = dict(call.attributes)
    attributes["argument_names"] = []
    broken = call.__class__(
        id=call.id,
        kind=call.kind,
        language=call.language,
        scope_id=call.scope_id,
        location=call.location,
        target=call.target,
        value=call.value,
        operands=call.operands,
        attributes=attributes,
    )
    errors = validate_semantic_ir([broken])
    assert errors == (f"{call.id}: argument_names must align with arguments",)


@pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")
def test_go_frontend_emits_valid_contract(tmp_path: Path):
    (tmp_path / "sample.go").write_text(
        "package sample\nfunc run(value string) string { return value }\n",
        encoding="utf-8",
    )
    ir = GoAdapter().parse_semantic_project(tmp_path)
    assert validate_semantic_ir(ir.operations) == ()
    assert ir.meta["semantic_contract"]["valid"] is True
