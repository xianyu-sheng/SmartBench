"""Conservative cross-file call and synchronization linking."""

from pathlib import Path

import pytest

from smartbench.analysis import InterproceduralGraph, SemanticLinker
from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
)
from smartbench.core.adapters import GoAdapter, PythonAdapter
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import DataFlowKind, OperationEdgeKind, OperationKind


def _linked_python(tmp_path: Path, sources: dict[str, str]):
    for name, source in sources.items():
        (tmp_path / name).write_text(source.strip(), encoding="utf-8")
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    result = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, result)
    return ir, result


def test_python_assignment_call_links_across_files(tmp_path: Path):
    ir, result = _linked_python(
        tmp_path,
        {
            "app.py": """
from worker import helper

def run():
    result = helper()
    return result
""",
            "worker.py": """
def helper():
    return 1
""",
        },
    )

    call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "helper"
    )
    edge = next(
        edge
        for edge in ir.operation_edges
        if edge.kind == OperationEdgeKind.CALLS and edge.source_id == call.id
    )
    target = next(operation for operation in ir.operations if operation.id == edge.target_id)

    assert target.attributes["qualified_name"] == "worker.helper"
    assert edge.attributes["resolution"] == "unique_simple"
    assert result.resolved_calls == 1
    assert result.ambiguous_calls == 0

    edge_count = len(ir.operation_edges)
    fact_count = len(ir.facts)
    SemanticLinker.apply(ir, result)
    assert len(ir.operation_edges) == edge_count
    assert len(ir.facts) == fact_count


def test_ambiguous_simple_name_does_not_invent_call_edge(tmp_path: Path):
    ir, result = _linked_python(
        tmp_path,
        {
            "app.py": """
def run():
    helper()
""",
            "one.py": """
def helper():
    return 1
""",
            "two.py": """
def helper():
    return 2
""",
        },
    )

    call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "helper"
    )
    assert not any(
        edge.kind == OperationEdgeKind.CALLS and edge.source_id == call.id
        for edge in ir.operation_edges
    )
    assert result.ambiguous_calls == 1


def test_python_self_call_uses_lexical_owner(tmp_path: Path):
    ir, result = _linked_python(
        tmp_path,
        {
            "service.py": """
class Service:
    def run(self):
        return self.helper()

    def helper(self):
        return 1
""",
        },
    )

    edge = next(edge for edge in result.edges if edge.kind == OperationEdgeKind.CALLS)
    target = next(operation for operation in ir.operations if operation.id == edge.target_id)
    assert target.attributes["qualified_name"] == "service.Service.helper"
    assert edge.attributes["resolution"] == "lexical_receiver"


def test_unknown_dotted_receiver_is_left_unresolved(tmp_path: Path):
    ir, result = _linked_python(
        tmp_path,
        {
            "service.py": """
def helper():
    return 1

def run(client):
    return client.helper()
""",
        },
    )

    call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "client.helper"
    )
    assert not any(
        edge.kind == OperationEdgeKind.CALLS and edge.source_id == call.id
        for edge in ir.operation_edges
    )
    assert result.unresolved_calls == 1


def test_python_type_proof_links_method_arguments_returns_and_query_path(
    tmp_path: Path,
):
    ir, result = _linked_python(
        tmp_path,
        {
            "service.py": """
class Service:
    def handle(self, value: str) -> str:
        return value

def make_service() -> Service:
    return Service()

def run(text: str):
    service = make_service()
    return service.handle(text)
""",
        },
    )

    method_call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "service.handle"
    )
    method_edge = next(
        edge
        for edge in ir.operation_edges
        if edge.kind == OperationEdgeKind.CALLS and edge.source_id == method_call.id
    )
    method = next(operation for operation in ir.operations if operation.id == method_edge.target_id)
    run = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.FUNCTION and operation.target == "run"
    )

    assert method.attributes["qualified_name"] == "service.Service.handle"
    assert method_edge.attributes["resolution"] == "typed_receiver"
    assert result.typed_receiver_calls == 1
    assert result.argument_edges == 1
    assert result.return_edges == 2

    graph = InterproceduralGraph(ir)
    bindings = graph.argument_bindings(method_call.id)
    assert len(bindings) == 1
    assert bindings[0].attributes["flow"] == DataFlowKind.ARGUMENT_TO_PARAMETER.value
    assert bindings[0].attributes["expression"] == "text"
    assert bindings[0].attributes["parameter"] == "value"
    assert [operation.value for operation in graph.return_sources(method_call.id)] == ["value"]
    assert [operation.target for operation in graph.call_path(run.id, method.id)] == [
        "run",
        "handle",
    ]
    assert graph.call_path(run.id, method.id, max_depth=0) == ()
    with pytest.raises(ValueError, match="max_depth"):
        graph.call_path(run.id, method.id, max_depth=33)


def test_ambiguous_typed_receiver_does_not_invent_method_edge(tmp_path: Path):
    ir, result = _linked_python(
        tmp_path,
        {
            "app.py": """
def run(service: Service):
    return service.handle()
""",
            "one.py": """
class Service:
    def handle(self):
        return 1
""",
            "two.py": """
class Service:
    def handle(self):
        return 2
""",
        },
    )

    call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "service.handle"
    )
    assert not any(
        edge.kind == OperationEdgeKind.CALLS and edge.source_id == call.id
        for edge in ir.operation_edges
    )
    assert result.ambiguous_calls == 1


def test_capitalized_python_function_is_not_treated_as_constructor(tmp_path: Path):
    ir, result = _linked_python(
        tmp_path,
        {
            "service.py": """
class Service:
    def handle(self):
        return 1

def Service():
    return object()

def run():
    service = Service()
    return service.handle()
""",
        },
    )

    assignment = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.ASSIGN and operation.target == "service"
    )
    method_call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "service.handle"
    )
    assert assignment.attributes["bindings"][0]["inferred_type"] == ""
    assert not any(
        edge.kind == OperationEdgeKind.CALLS and edge.source_id == method_call.id
        for edge in result.edges
    )


@pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")
def test_go_spawn_defer_and_channel_operations_are_linked(tmp_path: Path):
    (tmp_path / "worker.go").write_text(
        """
package sample

func worker() {}
func cleanup() {}

func run(ch chan int) {
    go worker()
    defer cleanup()
    ch <- 1
    value := <-ch
    _ = value
}
""".strip(),
        encoding="utf-8",
    )
    ir = GoAdapter().parse_semantic_project(tmp_path)
    result = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, result)

    call_edges = [edge for edge in result.edges if edge.kind == OperationEdgeKind.CALLS]
    assert {edge.attributes["call_kind"] for edge in call_edges} == {"spawn", "defer"}
    assert result.synchronization_edges == 1
    synchronization = next(
        edge for edge in result.edges if edge.kind == OperationEdgeKind.SYNCHRONIZES
    )
    assert synchronization.attributes == {
        "channel": "ch",
        "scope": "intraprocedural",
    }


@pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")
def test_go_return_type_propagates_to_receiver_and_parameter_binding(tmp_path: Path):
    (tmp_path / "runner.go").write_text(
        """
package sample

type Runner struct{}

func (runner *Runner) Run(value string) string {
    return value
}

func makeRunner() *Runner {
    return &Runner{}
}

func main() {
    runner := makeRunner()
    result := runner.Run("ok")
    _ = result
}
""".strip(),
        encoding="utf-8",
    )
    ir = GoAdapter().parse_semantic_project(tmp_path)
    result = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, result)

    method_call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "runner.Run"
    )
    method_edge = next(
        edge
        for edge in ir.operation_edges
        if edge.kind == OperationEdgeKind.CALLS and edge.source_id == method_call.id
    )
    target = next(operation for operation in ir.operations if operation.id == method_edge.target_id)

    assert target.attributes["qualified_name"] == "sample.Runner.Run"
    assert method_edge.attributes["resolution"] == "typed_receiver"
    assert method_call.attributes["arguments"] == ['"ok"']
    assert method_call.attributes["result_targets"] == ["result"]
    assert result.typed_receiver_calls == 1
    binding = InterproceduralGraph(ir).argument_bindings(method_call.id)
    assert len(binding) == 1
    assert binding[0].attributes["parameter"] == "value"


def test_unified_engine_applies_linker_and_rag_retrieves_link_fact(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "def run():\n    return helper()\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    adapters = AdapterRegistry()
    adapters.register(PythonAdapter())
    engine = UnifiedDiagnosticEngine(adapters, RuleRegistry())
    result = engine.diagnose(
        tmp_path,
        UnifiedDiagnosticConfig(
            use_static_rules=False,
            languages=["python"],
        ),
    )

    assert result.errors == []
    assert result.ir is not None
    assert result.ir.meta["semantic_linker"]["resolved_calls"] == 1
    assert result.stats["ir_call_edges"] == 1
    assert result.stats["ir_return_edges"] == 1
    assert result.stats["ir_data_dependency_edges"] == 1

    pack = DeterministicGraphRAG(result.ir).retrieve("helper interprocedural_call")
    assert any(fact.attributes.get("link_kind") == "interprocedural_call" for fact in pack.facts)
