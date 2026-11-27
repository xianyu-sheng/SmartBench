"""Bounded interprocedural proofs for resource ownership transfer."""

from pathlib import Path

import pytest

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocol
from smartbench.core.adapters import PythonAdapter
from smartbench.graph.schema import CodeGraph
from smartbench.ir import (
    EvidenceRef,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticIR,
    SemanticOperation,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROTOCOL = ResourceProtocol(
    acquire_symbol="open",
    resource_result_index=0,
    cleanup_methods=("close",),
)


def _parse(tmp_path: Path, source: str):
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    return PythonAdapter().parse_semantic_project(tmp_path)


def _analyze_source(tmp_path: Path, source: str):
    return ResourceLifecycleAnalyzer().analyze(_parse(tmp_path, source), [PROTOCOL])


def test_ownership_transfer_fixture_pair(tmp_path: Path):
    before = _analyze_source(
        tmp_path,
        (FIXTURES / "resource_ownership_transfer" / "before.py").read_text(
            encoding="utf-8"
        ),
    )
    after = _analyze_source(
        tmp_path,
        (FIXTURES / "resource_ownership_transfer" / "after.py").read_text(
            encoding="utf-8"
        ),
    )

    assert len(before.findings) == 1
    assert before.findings[0].first_unprotected_use.target == "consume"
    assert after.findings == []
    assert after.abstentions == 0


def test_wrapper_leak_fixture_pair_remains_findable(tmp_path: Path):
    before = _analyze_source(
        tmp_path,
        (FIXTURES / "wrapper_leak" / "before.py").read_text(encoding="utf-8"),
    )
    after = _analyze_source(
        tmp_path,
        (FIXTURES / "wrapper_leak" / "after.py").read_text(encoding="utf-8"),
    )

    assert len(before.findings) == 1
    assert len(after.findings) == 1
    assert after.findings[0].first_unprotected_use.target == "consume"
    assert after.abstentions == 0


@pytest.mark.parametrize(
    "helper",
    [
        "def close_resource(resource):\n    alias = resource\n    alias.close()",
        "def close_resource(resource):\n    defer_close(resource)",
    ],
)
def test_statically_provable_helper_cleanup_is_clean(tmp_path: Path, helper: str):
    source = f"""
{helper}

def defer_close(resource):
    resource.close()

def load(path):
    resource = open(path)
    close_resource(resource)
    return resource.read()
""".strip()

    result = _analyze_source(tmp_path, source)

    assert result.findings == []
    assert result.abstentions == 0


def test_conditional_cleanup_that_misses_a_path_is_a_wrapper_leak(tmp_path: Path):
    source = """
def maybe_close(resource, enabled):
    if enabled:
        resource.close()
    else:
        resource.read()

def load(path):
    resource = open(path)
    maybe_close(resource, True)
    return None
""".strip()

    result = _analyze_source(tmp_path, source)

    assert len(result.findings) == 1
    assert result.findings[0].first_unprotected_use.target == "maybe_close"
    assert result.abstentions == 0


def test_incomplete_conditional_cleanup_abstains(tmp_path: Path):
    source = """
def maybe_close(resource, enabled):
    if enabled:
        resource.close()

def load(path):
    resource = open(path)
    maybe_close(resource, True)
    return None
""".strip()

    result = _analyze_source(tmp_path, source)

    assert result.findings == []
    assert result.abstentions == 1
    assert "incomplete branch outcome" in result.unknown_reasons[0]


def test_unresolved_ownership_candidate_abstains(tmp_path: Path):
    source = """
def load(path):
    resource = open(path)
    external_consume(resource)
    return None
""".strip()

    result = _analyze_source(tmp_path, source)

    assert result.findings == []
    assert result.abstentions == 1
    assert "ownership of resource" in result.unknown_reasons[0]
    assert "external_consume" in result.unknown_reasons[0]
    assert "undecidable" in result.unknown_reasons[0]


def test_caller_alias_can_transfer_ownership(tmp_path: Path):
    source = """
def close_resource(resource):
    resource.close()

def load(path):
    resource = open(path)
    alias = resource
    close_resource(alias)
    return alias.read()
""".strip()

    result = _analyze_source(tmp_path, source)

    assert result.findings == []
    assert result.abstentions == 0


def test_caller_alias_wrapper_leak_remains_findable(tmp_path: Path):
    source = """
def consume(resource):
    resource.read()

def load(path):
    resource = open(path)
    alias = resource
    consume(alias)
    return None
""".strip()

    result = _analyze_source(tmp_path, source)

    assert len(result.findings) == 1
    assert result.findings[0].first_unprotected_use.target == "consume"
    assert result.abstentions == 0


def test_alias_cleanup_before_alias_assignment_cannot_prove_transfer(tmp_path: Path):
    source = """
def wrapper(resource):
    alias = other_resource()
    alias.Close()
    alias = resource
    return None

def load(path):
    resource = open(path)
    wrapper(resource)
    return None
""".strip()
    protocol = ResourceProtocol(
        acquire_symbol="open",
        resource_result_index=0,
        cleanup_methods=("Close",),
    )

    result = ResourceLifecycleAnalyzer().analyze(
        _parse(tmp_path, source), [protocol]
    )

    assert len(result.findings) == 1
    assert result.findings[0].first_unprotected_use.target == "wrapper"
    assert result.abstentions == 0


def test_named_argument_maps_to_helper_parameter(tmp_path: Path):
    source = """
def close_resource(resource):
    resource.close()

def load(path):
    resource = open(path)
    close_resource(resource=resource)
    return resource.read()
""".strip()

    result = _analyze_source(tmp_path, source)

    assert result.findings == []
    assert result.abstentions == 0


@pytest.mark.parametrize("pass_member", [False, True])
def test_resource_member_transfer_uses_effective_cleanup_path(
    tmp_path: Path,
    pass_member: bool,
):
    helper_argument = "body" if pass_member else "response"
    cleanup_receiver = "body.Close" if pass_member else "response.Body.Close"
    call_argument = "response.Body" if pass_member else "response"
    source = f"""
def close_resource({helper_argument}):
    {cleanup_receiver}()

def load():
    response = open("resource")
    close_resource({call_argument})
    return response.Body.read()
""".strip()
    protocol = ResourceProtocol(
        acquire_symbol="open",
        resource_result_index=0,
        cleanup_methods=("Close",),
        resource_member_path="Body",
    )

    result = ResourceLifecycleAnalyzer().analyze(
        _parse(tmp_path, source), [protocol]
    )

    assert result.findings == []
    assert result.abstentions == 0


def test_ambiguous_local_function_abstains(tmp_path: Path):
    (tmp_path / "sample.py").write_text(
        """
def load(path):
    resource = open(path)
    close_resource(resource)
    return None
""".strip(),
        encoding="utf-8",
    )
    for name in ("first", "second"):
        (tmp_path / f"{name}.py").write_text(
            "def close_resource(resource):\n    resource.close()\n",
            encoding="utf-8",
        )

    result = ResourceLifecycleAnalyzer().analyze(
        PythonAdapter().parse_semantic_project(tmp_path), [PROTOCOL]
    )

    assert result.findings == []
    assert result.abstentions == 1
    assert "multiple local free functions" in result.unknown_reasons[0]


def test_returned_alias_without_caller_cleanup_abstains(tmp_path: Path):
    source = """
def identity(resource):
    return resource

def load(path):
    resource = open(path)
    identity(resource)
    return None
""".strip()

    result = _analyze_source(tmp_path, source)

    assert result.findings == []
    assert result.abstentions == 1
    assert "returns an aliased resource" in result.unknown_reasons[0]


def test_use_before_proven_transfer_remains_findable(tmp_path: Path):
    source = """
def close_resource(resource):
    resource.close()

def load(path):
    resource = open(path)
    resource.read()
    close_resource(resource)
    return None
""".strip()

    result = _analyze_source(tmp_path, source)

    assert len(result.findings) == 1
    assert result.findings[0].first_unprotected_use.target == "resource.read"


@pytest.mark.parametrize(
    ("helper_method", "expected_findings"),
    [("Close", 0), ("Read", 1)],
)
def test_go_shaped_operations_use_the_same_language_neutral_proof(
    helper_method: str,
    expected_findings: int,
):
    def operation(
        operation_id: str,
        kind: OperationKind,
        scope_id: str,
        line: int,
        *,
        target: str = "",
        value: str = "",
        attributes: dict[str, object] | None = None,
    ) -> SemanticOperation:
        return SemanticOperation(
            id=operation_id,
            kind=kind,
            language="go",
            scope_id=scope_id,
            location=EvidenceRef("sample.go", line),
            target=target,
            value=value,
            attributes=attributes or {},
        )

    helper = operation(
        "helper", OperationKind.FUNCTION, "helper", 1, target="transfer"
    )
    parameter = operation(
        "parameter",
        OperationKind.PARAMETER,
        "helper",
        1,
        target="file",
        attributes={"position": 0, "parameter_kind": "positional"},
    )
    helper_call = operation(
        "helper-call",
        OperationKind.CALL,
        "helper",
        2,
        target=f"file.{helper_method}",
        attributes={"receiver": "file", "arguments": []},
    )
    assign = operation(
        "assign",
        OperationKind.ASSIGN,
        "load",
        5,
        target="file",
        value="file, err := os.Open(path)",
        attributes={"calls": ["os.Open"]},
    )
    acquire = operation(
        "acquire",
        OperationKind.CALL,
        "load",
        5,
        target="os.Open",
        value="os.Open(path)",
        attributes={
            "host_operation": "assign",
            "result_targets": ["file", "err"],
            "arguments": ["path"],
        },
    )
    transfer = operation(
        "transfer",
        OperationKind.CALL,
        "load",
        6,
        target="transfer",
        value="transfer(file)",
        attributes={"arguments": ["file"]},
    )
    use = operation(
        "use",
        OperationKind.CALL,
        "load",
        7,
        target="file.Read",
        attributes={"receiver": "file", "arguments": []},
    )
    ir = SemanticIR.from_graph(CodeGraph(), language="go")
    ir.operations = [helper, parameter, helper_call, assign, acquire, transfer, use]
    ir.operation_edges = [
        OperationEdge("acquire", "assign", OperationEdgeKind.NEXT),
        OperationEdge("assign", "transfer", OperationEdgeKind.NEXT),
        OperationEdge("transfer", "use", OperationEdgeKind.NEXT),
    ]
    protocol = ResourceProtocol(
        acquire_symbol="os.Open",
        resource_result_index=0,
        cleanup_methods=("Close",),
    )

    result = ResourceLifecycleAnalyzer().analyze(ir, [protocol])

    assert len(result.findings) == expected_findings
    assert result.abstentions == 0
    if expected_findings:
        assert result.findings[0].first_unprotected_use.id == "transfer"
