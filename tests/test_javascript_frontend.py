"""Conformance tests for the shared JavaScript/TypeScript frontend."""

from pathlib import Path

import pytest

from smartbench.analysis import SemanticLinker
from smartbench.core.adapters import JavaScriptAdapter, TypeScriptAdapter
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    Capability,
    CapabilityLevel,
    OperationEdgeKind,
    OperationKind,
    validate_semantic_ir,
)

HAS_ECMASCRIPT_PARSERS = all(
    get_parser(language) is not None for language in ("javascript", "typescript")
)


@pytest.mark.skipif(not HAS_ECMASCRIPT_PARSERS, reason="tree-sitter JavaScript/TypeScript unavailable")
@pytest.mark.parametrize(
    ("language", "adapter", "filename", "source"),
    [
        (
            "javascript",
            JavaScriptAdapter(),
            "app.js",
            """
class Store {
  async load(request) {
    const id = request.query.id;
    if (id) { return db.query(id); }
    return null;
  }
}
""",
        ),
        (
            "typescript",
            TypeScriptAdapter(),
            "app.ts",
            """
export async function load(request: Request): Promise<string> {
  const id: string = request.query.id;
  if (!id) { return ""; }
  return db.query(id);
}
const normalize = (value: string) => value.trim();
""",
        ),
    ],
)
def test_ecmascript_frontend_emits_common_operations(
    tmp_path: Path,
    language: str,
    adapter,
    filename: str,
    source: str,
):
    (tmp_path / filename).write_text(source, encoding="utf-8")
    ir = adapter.parse_semantic_project(tmp_path)

    kinds = {operation.kind for operation in ir.operations}
    assert OperationKind.FUNCTION in kinds
    assert OperationKind.PARAMETER in kinds
    assert OperationKind.ASSIGN in kinds
    assert OperationKind.BRANCH in kinds
    assert OperationKind.CALL in kinds
    assert validate_semantic_ir(ir.operations) == ()
    assert ir.meta["javascript_frontend"]["errors"] == []
    assert ir.meta["javascript_frontend"]["operations"] == len(ir.operations)
    assert ir.capabilities[language].level(Capability.CONTROL_FLOW) == CapabilityLevel.PARTIAL

    functions = [operation for operation in ir.operations if operation.kind == OperationKind.FUNCTION]
    assert any(operation.attributes["qualified_name"] for operation in functions)
    calls = [operation for operation in ir.operations if operation.kind == OperationKind.CALL]
    assert any(operation.target == "db.query" for operation in calls)


@pytest.mark.skipif(not HAS_ECMASCRIPT_PARSERS, reason="tree-sitter TypeScript unavailable")
def test_typescript_operations_use_shared_interprocedural_contract(tmp_path: Path):
    (tmp_path / "app.ts").write_text(
        """
export function helper(value: string): string { return value; }
export function run(input: string): string {
  const result: string = helper(input);
  return result;
}
""",
        encoding="utf-8",
    )
    ir = TypeScriptAdapter().parse_semantic_project(tmp_path)
    linked = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, linked)

    call = next(
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target == "helper"
    )
    assert call.attributes["result_targets"] == ["result"]
    assert call.attributes["host_operation"]
    assert linked.to_dict()["call_edges"] == 1
    assert linked.argument_edges == 1
    assert linked.return_edges == 1
    assert any(edge.kind == OperationEdgeKind.CALLS for edge in ir.operation_edges)
