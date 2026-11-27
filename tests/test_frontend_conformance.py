"""Parameterized conformance checks for language frontends."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from smartbench.analysis import (
    InvariantKind,
    OperationSelector,
    StateInvariant,
    StateMachineAnalyzer,
)
from smartbench.core.adapters import (
    GoAdapter,
    JavaScriptAdapter,
    LanguageAdapter,
    PythonAdapter,
)
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    Capability,
    EvidenceRef,
    OperationEdgeKind,
    OperationKind,
    SemanticIR,
)

FIXTURES = Path(__file__).parent / "fixtures" / "frontend_conformance"


@dataclass(frozen=True)
class FrontendCase:
    adapter: LanguageAdapter
    fixture_dir: Path


PYTHON_CASE = FrontendCase(PythonAdapter(), FIXTURES / "python")
GO_CASE = FrontendCase(GoAdapter(), FIXTURES / "go")
JAVASCRIPT_CASE = FrontendCase(JavaScriptAdapter(), FIXTURES / "javascript")
FRONTEND_CASE_VALUES = (PYTHON_CASE, GO_CASE, JAVASCRIPT_CASE)

FRONTEND_CASES = [
    pytest.param(PYTHON_CASE, id="python"),
    pytest.param(
        GO_CASE,
        id="go",
        marks=pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable"),
    ),
    pytest.param(
        JAVASCRIPT_CASE,
        id="javascript",
        marks=pytest.mark.skipif(
            get_parser("javascript") is None,
            reason="tree-sitter JavaScript unavailable",
        ),
    ),
]

MALFORMED_CASES = [
    pytest.param(PYTHON_CASE, id="python"),
    pytest.param(
        GO_CASE,
        id="go",
        marks=[
            pytest.mark.skipif(
                get_parser("go") is None,
                reason="tree-sitter Go unavailable",
            ),
            pytest.mark.xfail(
                reason="Go lowering does not surface tree-sitter error nodes (issue #73)",
                strict=True,
            ),
        ],
    ),
    pytest.param(
        JAVASCRIPT_CASE,
        id="javascript",
        marks=[
            pytest.mark.skipif(
                get_parser("javascript") is None,
                reason="tree-sitter JavaScript unavailable",
            ),
            pytest.mark.xfail(
                reason="JavaScript lowering does not surface tree-sitter error nodes (issue #73)",
                strict=True,
            ),
        ],
    ),
]


def _parse_snapshot(case: FrontendCase, snapshot: str, project_dir: Path) -> SemanticIR:
    project_dir.mkdir(parents=True, exist_ok=True)
    extension = case.adapter.file_extensions[0]
    source_name = "malformed.txt" if snapshot == "malformed" else f"{snapshot}{extension}"
    source = case.fixture_dir / source_name
    target = project_dir / f"{snapshot}{extension}"
    target.write_bytes(source.read_bytes())
    return case.adapter.parse_semantic_project(project_dir)


def _stable_ir_json(ir: SemanticIR) -> str:
    payload = ir.to_dict()
    payload["project_path"] = "<project>"
    graph_meta = payload["graph"]["meta"]
    graph_meta["project_path"] = "<project>"
    for key in list(graph_meta):
        if (
            key in {"build_time_ms", "semantic_contract"}
            or key.endswith("_frontend")
            or key.endswith("_type_evidence")
        ):
            graph_meta.pop(key, None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _frontend_errors(ir: SemanticIR) -> list[str]:
    errors: list[str] = []
    for name, metadata in ir.meta.items():
        if not name.endswith("_frontend") or not isinstance(metadata, dict):
            continue
        failures = metadata.get("errors", [])
        if isinstance(failures, list):
            errors.extend(str(failure) for failure in failures)
    return errors


def _source_span(lines: list[str], reference: EvidenceRef) -> str:
    assert reference.line_end is not None
    assert reference.column_start is not None
    assert reference.column_end is not None
    first = lines[reference.line_start - 1]
    last = lines[reference.line_end - 1]
    if reference.line_start == reference.line_end:
        return first[reference.column_start : reference.column_end]
    return "\n".join(
        [
            first[reference.column_start :],
            *lines[reference.line_start : reference.line_end - 1],
            last[: reference.column_end],
        ]
    )


def _assert_structure(ir: SemanticIR) -> None:
    assert ir.graph.nodes
    assert any(operation.kind == OperationKind.FUNCTION for operation in ir.operations)


def _assert_source_locations(ir: SemanticIR) -> None:
    assert ir.operations
    for operation in ir.operations:
        assert operation.location.file_path
        assert operation.location.line_start >= 1
        assert operation.location.snippet


def _assert_symbols(ir: SemanticIR) -> None:
    functions = [operation for operation in ir.operations if operation.kind == OperationKind.FUNCTION]
    calls = [operation for operation in ir.operations if operation.kind == OperationKind.CALL]
    assert functions
    assert calls
    assert all(operation.target for operation in functions)
    assert all(operation.target for operation in calls)


def _assert_control_flow(ir: SemanticIR) -> None:
    assert any(operation.kind == OperationKind.BRANCH for operation in ir.operations)
    edge_kinds = {edge.kind for edge in ir.operation_edges}
    assert OperationEdgeKind.TRUE_BRANCH in edge_kinds
    assert OperationEdgeKind.FALSE_BRANCH in edge_kinds


FULL_CAPABILITY_ASSERTIONS: dict[Capability, Callable[[SemanticIR], None]] = {
    Capability.STRUCTURE: _assert_structure,
    Capability.SOURCE_LOCATIONS: _assert_source_locations,
    Capability.SYMBOLS: _assert_symbols,
    Capability.CONTROL_FLOW: _assert_control_flow,
}


def _cleanup_invariant() -> StateInvariant:
    return StateInvariant(
        invariant_id="release-before-consume",
        kind=InvariantKind.REQUIRE_GUARD_BEFORE_ACTION,
        event=OperationSelector.of(OperationKind.CALL, contains_all=("acquire",)),
        guard=OperationSelector.of(OperationKind.CALL, contains_all=("release",)),
        action=OperationSelector.of(OperationKind.CALL, contains_all=("consume",)),
        message="acquired resources must be released before use",
    )


@pytest.mark.parametrize("case", FRONTEND_CASES)
@pytest.mark.parametrize("snapshot", ["before", "after"])
def test_source_locations_round_trip(
    case: FrontendCase,
    snapshot: str,
    tmp_path: Path,
):
    ir = _parse_snapshot(case, snapshot, tmp_path)

    assert ir.operations
    for operation in ir.operations:
        reference = operation.location
        source_path = tmp_path / reference.file_path
        assert source_path.is_file()
        lines = source_path.read_text(encoding="utf-8").splitlines()
        assert 1 <= reference.line_start <= reference.line_end <= len(lines)
        assert reference.snippet == _source_span(lines, reference)


@pytest.mark.parametrize("case", FRONTEND_CASES)
def test_claimed_full_capabilities_have_behavioral_assertions(
    case: FrontendCase,
    tmp_path: Path,
):
    ir = _parse_snapshot(case, "before", tmp_path)
    capabilities = ir.capabilities[case.adapter.language]

    assert capabilities.language == case.adapter.language
    assert not set(capabilities.supported).intersection(capabilities.partial)
    assert all(reason.strip() for reason in capabilities.partial.values())

    unchecked = set(capabilities.supported).difference(FULL_CAPABILITY_ASSERTIONS)
    assert not unchecked, f"full capabilities without behavioral assertions: {unchecked}"
    for capability in sorted(capabilities.supported, key=lambda item: item.value):
        FULL_CAPABILITY_ASSERTIONS[capability](ir)


@pytest.mark.parametrize("case", FRONTEND_CASES)
@pytest.mark.parametrize("snapshot", ["before", "after"])
def test_ir_serialization_is_stable_and_matches_golden(
    case: FrontendCase,
    snapshot: str,
    tmp_path: Path,
):
    if case.adapter.language == "python" and get_parser("python") is None:
        pytest.skip("golden IR requires the tree-sitter Python backend")
    first = _parse_snapshot(case, snapshot, tmp_path / "first")
    second = _parse_snapshot(case, snapshot, tmp_path / "second")

    serialized = _stable_ir_json(first)
    assert serialized == _stable_ir_json(second)
    golden = case.fixture_dir / f"golden_{snapshot}.json"
    assert serialized == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", MALFORMED_CASES)
def test_malformed_source_surfaces_parse_error(case: FrontendCase, tmp_path: Path):
    ir = _parse_snapshot(case, "malformed", tmp_path)

    assert _frontend_errors(ir)


@pytest.mark.parametrize("case", FRONTEND_CASES)
def test_language_neutral_analyzer_distinguishes_before_and_after(
    case: FrontendCase,
    tmp_path: Path,
):
    before_ir = _parse_snapshot(case, "before", tmp_path / "before")
    after_ir = _parse_snapshot(case, "after", tmp_path / "after")
    invariant = _cleanup_invariant()

    before = StateMachineAnalyzer().analyze(before_ir, [invariant])
    after = StateMachineAnalyzer().analyze(after_ir, [invariant])

    assert len(before.violations) == 1
    assert before.violations[0].missing == "guard"
    assert after.violations == []
