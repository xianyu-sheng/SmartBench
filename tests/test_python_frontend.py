"""Python frontend lowering into language-neutral semantic operations."""

from pathlib import Path

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocolMiner
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

    branch = next(
        operation for operation in ir.operations if operation.kind == OperationKind.BRANCH
    )
    assert "usage.finish_reason" in branch.operands
    assert "'stop'" in branch.attributes["literals"]
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
        if operation.kind == OperationKind.CALL and operation.target == "retry_message"
    )
    assert function.attributes["return_types"] == ["None"]
    assert parameters[1].attributes["declared_type"] == "str"
    assert parameters[1].attributes["position"] == 1
    assert call.attributes["arguments"] == []


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
        fact.attributes.get("operation_kind") == OperationKind.BRANCH.value for fact in pack.facts
    )
    assert any(reference.source == "python_frontend" for reference in pack.evidence)


def test_python_context_manager_lowers_to_resource_protocol(tmp_path: Path):
    source = """
def load(path):
    with open(path) as handle:
        return handle.read()
""".strip()
    (tmp_path / "loader.py").write_text(source, encoding="utf-8")

    ir = PythonAdapter().parse_semantic_project(tmp_path)
    operations = ir.operations
    assignment = next(
        operation
        for operation in operations
        if operation.kind == OperationKind.ASSIGN
        and operation.attributes.get("context_manager") is True
    )
    acquire = next(
        operation
        for operation in operations
        if operation.kind == OperationKind.CALL and operation.target == "open"
    )
    cleanup = next(
        operation
        for operation in operations
        if operation.kind == OperationKind.DEFER
        and operation.attributes.get("context_manager") is True
    )

    assert assignment.attributes["bindings"] == [
        {"target": "handle", "declared_type": "", "inferred_type": ""}
    ]
    assert acquire.attributes["result_targets"] == ["handle"]
    assert cleanup.target == "handle.__exit__"
    assert cleanup.attributes["context_manager_kind"] == "sync"
    assert any(
        edge.source_id == acquire.id
        and edge.target_id == cleanup.id
        and edge.kind == OperationEdgeKind.NEXT
        for edge in ir.operation_edges
    )

    protocols = ResourceProtocolMiner().learn(ir)
    assert [(protocol.acquire_symbol, protocol.cleanup_methods) for protocol in protocols] == [
        ("open", ("__exit__",))
    ]
    assert ResourceLifecycleAnalyzer().analyze(ir, protocols).findings == []

    leaked = """
def load(path):
    handle = open(path)
    return handle.read()
""".strip()
    (tmp_path / "loader.py").write_text(leaked, encoding="utf-8")
    leaked_ir = PythonAdapter().parse_semantic_project(tmp_path)
    result = ResourceLifecycleAnalyzer().analyze(leaked_ir, protocols)
    assert len(result.findings) == 1
    assert result.findings[0].resource_binding == "handle"


def test_python_async_context_manager_preserves_async_protocol(tmp_path: Path):
    source = """
async def load(client):
    async with client.stream() as response:
        return await response.aread()
""".strip()
    (tmp_path / "loader.py").write_text(source, encoding="utf-8")

    ir = PythonAdapter().parse_semantic_project(tmp_path)
    cleanup = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.DEFER
        and operation.attributes.get("context_manager") is True
    )
    assert cleanup.target == "response.__aexit__"
    assert cleanup.attributes["context_manager_kind"] == "async"
    assert cleanup.attributes["enter_method"] == "__aenter__"


def test_python_async_await_and_exception_paths_are_explicit(tmp_path: Path):
    source = """
async def worker(client):
    try:
        await client.fetch()
    except TimeoutError as exc:
        raise RuntimeError("retry") from exc
    finally:
        await client.close()
""".strip()
    (tmp_path / "worker.py").write_text(source, encoding="utf-8")

    ir = PythonAdapter().parse_semantic_project(tmp_path)
    calls = {operation.target: operation for operation in ir.operations if operation.kind == OperationKind.CALL}
    raised = next(operation for operation in ir.operations if operation.kind == OperationKind.RAISE)
    marker = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.BRANCH and operation.attributes.get("construct") == "try"
    )

    assert calls["client.fetch"].attributes["awaited"] is True
    assert calls["client.close"].attributes["awaited"] is True
    assert raised.attributes == {
        "exception": "RuntimeError('retry')",
        "cause": "exc",
        "bare": False,
    }
    assert marker.attributes["exception_types"] == ["TimeoutError"]
    assert any(
        edge.source_id == raised.id
        and edge.target_id == calls["client.close"].id
        and edge.kind == OperationEdgeKind.NEXT
        for edge in ir.operation_edges
    )


def test_python_return_path_runs_finally_before_function_exit(tmp_path: Path):
    source = """
def load(path):
    try:
        return read(path)
    finally:
        close(path)
""".strip()
    (tmp_path / "loader.py").write_text(source, encoding="utf-8")

    ir = PythonAdapter().parse_semantic_project(tmp_path)
    returned = next(operation for operation in ir.operations if operation.kind == OperationKind.RETURN)
    cleanup = next(operation for operation in ir.operations if operation.target == "close")
    assert any(
        edge.source_id == returned.id
        and edge.target_id == cleanup.id
        and edge.kind == OperationEdgeKind.NEXT
        for edge in ir.operation_edges
    )
