"""Go surface types feed the language-neutral evidence contract conservatively."""

from pathlib import Path

import pytest

from smartbench.core.adapters import GoAdapter
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    OperationKind,
    TypeEvidenceIndex,
    TypeEvidenceRole,
    TypeEvidenceSource,
)

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


def _calls(ir, suffix: str):
    return [
        operation
        for operation in ir.operations
        if operation.kind == OperationKind.CALL and operation.target.endswith(suffix)
    ]


def test_struct_field_and_local_assignment_prove_receiver_identity(tmp_path: Path):
    source = """
package sample

import stdhttp "net/http"

type Service struct {
    client *stdhttp.Client
}

func fetch(service *Service, req *stdhttp.Request) error {
    local := service.client
    response, err := local.Do(req)
    if err != nil { return err }
    return consume(response.Body)
}
""".strip()
    (tmp_path / "client.go").write_text(source, encoding="utf-8")

    ir = GoAdapter().parse_semantic_project(tmp_path)
    call = _calls(ir, ".Do")[0]
    evidence = TypeEvidenceIndex(ir.type_evidence).for_operation(
        call.id, TypeEvidenceRole.RECEIVER
    )

    assert len(evidence) == 1
    assert evidence[0].normalized_type == "net/http.Client"
    assert evidence[0].canonical_symbol == "net/http.Client.Do"
    assert evidence[0].source == TypeEvidenceSource.LOCAL_PROPAGATION
    assert {ref.line_start for ref in evidence[0].evidence} >= {6, 10, 11}
    assert ir.meta["go_type_evidence"]["surface_only"] is True
    assert ir.meta["go_type_evidence"]["errors"] == []


def test_same_package_name_in_different_directories_does_not_merge_fields(
    tmp_path: Path,
):
    for directory, import_path in (("first", "net/http"), ("second", "example/client")):
        target = tmp_path / directory
        target.mkdir()
        (target / "client.go").write_text(
            f'''package sample

import clientpkg "{import_path}"

type Service struct {{
    client *clientpkg.Client
}}

func fetch(service *Service, req any) {{
    service.client.Do(req)
}}
'''.strip(),
            encoding="utf-8",
        )

    ir = GoAdapter().parse_semantic_project(tmp_path)
    index = TypeEvidenceIndex(ir.type_evidence)
    types = {
        call.location.file_path: index.unique_type(call.id, TypeEvidenceRole.RECEIVER)
        for call in _calls(ir, ".Do")
    }

    assert types == {
        "first/client.go": "net/http.Client",
        "second/client.go": "example/client.Client",
    }
