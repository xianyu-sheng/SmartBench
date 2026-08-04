"""Go type-checker evidence provider tests.

The provider shells out to the bundled ``typeprobe`` helper when the Go
toolchain is available; without it the provider must degrade to emitting
nothing rather than inventing types.
"""

from pathlib import Path

import pytest

from smartbench.core.adapters import GoAdapter
from smartbench.frontends.go_type_checker import (
    GoTypeCheckerProvider,
    _useless_probe_type,
)
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    OperationKind,
    TypeEvidenceIndex,
    TypeEvidenceRole,
    TypeEvidenceSource,
)

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")

_STDLIB_FIXTURE = '''\
package sample

import (
    "net/http"
    "os"
)

func fetch(httpClient *http.Client, req *http.Request, path string) error {
    resp, err := httpClient.Do(req)
    if err != nil { return err }
    defer resp.Body.Close()
    f, err := os.Open(path)
    if err != nil { return err }
    defer f.Close()
    return nil
}
'''


def _write_fixture(tmp_path: Path, source: str = _STDLIB_FIXTURE) -> Path:
    (tmp_path / "go.mod").write_text("module sample\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(source, encoding="utf-8")
    return tmp_path


def test_useless_probe_type_filters_surfaces():
    assert _useless_probe_type("")
    assert _useless_probe_type("invalid type")
    assert _useless_probe_type("(*os.File, error)")  # multi-value signature
    assert _useless_probe_type("func(string) (*os.File, error)")
    assert not _useless_probe_type("*net/http.Client")
    assert not _useless_probe_type("io.Reader")
    assert not _useless_probe_type("io.ReadCloser")


class _FakeProbeProvider(GoTypeCheckerProvider):
    """Provider with a scripted probe response; no subprocess."""

    def __init__(self, responses: dict):
        super().__init__(probe_binary="/fake/typeprobe")
        self._responses = responses
        self.probed_queries: list[dict] = []

    def _run_probe(self, queries):
        self.probed_queries = list(queries)
        return [dict(self._responses.get(_qkey(q), {"error": "not found"})) for q in queries]


def _qkey(query):
    return (query["file"], query["line"], query["symbol"])


def test_provider_emits_type_checker_evidence_for_receiver(tmp_path: Path):
    project = _write_fixture(tmp_path)
    ir = GoAdapter().parse_semantic_project(project)

    call = next(
        op
        for op in ir.operations
        if op.kind == OperationKind.CALL and op.target.endswith(".Do")
    )
    assert call.attributes.get("receiver") == "httpClient"

    fake = _FakeProbeProvider(
        {
            ("main.go", call.location.line_start, "httpClient"): {
                "declared_type": "*net/http.Client",
                "has_close_method": False,
                "object_kind": "use",
            }
        }
    )
    result = fake.provide(ir)

    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.source == TypeEvidenceSource.TYPE_CHECKER
    assert evidence.provider == "go.typechecker"
    assert evidence.role == TypeEvidenceRole.RECEIVER
    assert evidence.operation_id == call.id
    assert evidence.normalized_type == "net/http.Client"
    assert evidence.attributes["has_close_method"] is False


def test_provider_filters_package_receiver_and_unresolved(tmp_path: Path):
    project = _write_fixture(tmp_path)
    ir = GoAdapter().parse_semantic_project(project)

    fake = _FakeProbeProvider({})  # everything unresolved
    result = fake.provide(ir)

    # No evidence, but no crash either — degradation is silent.
    assert result.evidence == []
    assert result.queries >= 1


def test_provider_absent_binary_degrades_gracefully(tmp_path: Path):
    project = _write_fixture(tmp_path)
    provider = GoTypeCheckerProvider(probe_binary="/nonexistent/typeprobe")
    ir = GoAdapter().parse_semantic_project(project)

    result = provider.provide(ir)
    assert result.evidence == []
    assert result.errors  # recorded, not silent


def _typeprobe_available() -> bool:
    from smartbench.frontends.go_type_checker import _auto_build_probe, _find_probe_binary

    return _find_probe_binary() is not None or _auto_build_probe() is not None


@pytest.mark.skipif(not _typeprobe_available(), reason="typeprobe binary unavailable")
def test_integration_resolves_stdlib_receiver_types(tmp_path: Path):
    """Real typeprobe run: receiver types resolve through go/types."""
    project = _write_fixture(tmp_path)
    ir = GoAdapter().parse_semantic_project(project)
    meta = ir.meta.get("go_type_evidence", {})

    type_checker = [
        evidence
        for evidence in ir.type_evidence
        if evidence.source == TypeEvidenceSource.TYPE_CHECKER
    ]
    by_binding = {
        evidence.binding: evidence.normalized_type for evidence in type_checker
    }
    # httpClient receiver resolves through go/types to net/http.Client.
    assert by_binding.get("httpClient") == "net/http.Client"
    # The os package receiver is filtered out.
    assert "os" not in by_binding
    assert meta.get("probe_available") is True


def test_type_checker_evidence_preferred_by_index(tmp_path: Path):
    """TypeEvidenceIndex prefers higher-rank type-checker evidence."""
    project = _write_fixture(tmp_path)
    ir = GoAdapter().parse_semantic_project(project)

    call = next(
        op
        for op in ir.operations
        if op.kind == OperationKind.CALL and op.target.endswith(".Do")
    )
    fake = _FakeProbeProvider(
        {
            ("main.go", call.location.line_start, "httpClient"): {
                "declared_type": "*net/http.Client",
                "has_close_method": False,
                "object_kind": "use",
            }
        }
    )
    result = fake.provide(ir)
    ir.type_evidence.extend(result.evidence)

    index = TypeEvidenceIndex(ir.type_evidence)
    unique = index.unique_type(call.id, TypeEvidenceRole.RECEIVER)
    # TYPE_CHECKER (rank 3) beats surface evidence (rank <= 2).
    assert unique == "net/http.Client"
