"""CrossChecker resource_type claim verification tests.

The type-checker evidence provider resolves symbols to their true types
through go/types.  CrossChecker now checks resource-lifecycle claims against
that evidence: a claim that "resp.File needs Close" is rejected when the
resolved type is io.Reader (no Close method), even though the file:line
exists.
"""

from pathlib import Path

import pytest

from smartbench.core.adapters import GoAdapter
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    TypeEvidence,
    TypeEvidenceRole,
    TypeEvidenceSource,
)
from smartbench.verifier import VerificationStatus
from smartbench.verifier.cross_checker import CrossChecker

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")

_SDK_FIXTURE = {
    "go.mod": "module closureverify\n\ngo 1.22\n",
    "sdk/sdk.go": '''package sdk

import "io"

type Resp struct {
    File     io.Reader
    FileName string
}

func GetResource() (*Resp, error) { return &Resp{}, nil }
''',
    "main/main.go": '''package main

import (
    "closureverify/sdk"
    "fmt"
    "io"
)

func fetch() error {
    resp, err := sdk.GetResource()
    if err != nil {
        return err
    }
    data, err := readAll(resp.File)
    if err != nil {
        return err
    }
    fmt.Println(len(data))
    return nil
}

func readAll(r io.Reader) ([]byte, error) { return nil, nil }
''',
}


def _write_sdk_fixture(tmp_path: Path) -> Path:
    for rel, content in _SDK_FIXTURE.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def _make_cross_checker(tmp_path: Path):
    """Build a CrossChecker wired with real type-checker evidence."""
    project = _write_sdk_fixture(tmp_path)
    ir = GoAdapter().parse_semantic_project(project)

    class _GraphStub:
        project_path = str(project)

        def find_by_name(self, name):
            return []


    checker = CrossChecker(
        _GraphStub(),
        str(project),
        graph_retriever=None,
        hybrid_retriever=None,
        type_evidence=ir.type_evidence,
    )
    return checker, ir


def test_resource_type_claim_rejected_for_io_reader(tmp_path: Path):
    """A claim that resp.File needs Close is rejected when the resolved
    type is io.Reader — even though the location exists."""
    checker, ir = _make_cross_checker(tmp_path)

    # Sanity: type-checker evidence for resp exists.
    resp_evidence = [
        e for e in ir.type_evidence
        if e.source == TypeEvidenceSource.TYPE_CHECKER and e.binding == "resp"
    ]
    assert resp_evidence, "expected TYPE_CHECKER evidence for resp"

    result = checker._verify_type_claim(
        {"type": "resource_type", "target": "resp.File", "expects_close": True}
    )
    assert result.status == VerificationStatus.HALLUCINATED
    # Rejected because the (prefix-fallback) resolved type has no Close method.
    assert "类型证据反驳" in result.detail
    assert "has_close_method=False" in result.detail


def test_resource_type_claim_verified_for_real_closer(tmp_path: Path):
    """A claim on a genuine resource type with Close is verified."""
    checker, ir = _make_cross_checker(tmp_path)

    # Add synthetic evidence: a symbol that IS a closer (like *os.File).
    evidence = TypeEvidence(
        operation_id="op-test",
        role=TypeEvidenceRole.RESULT,
        type_name="*os.File",
        source=TypeEvidenceSource.TYPE_CHECKER,
        provider="go.typechecker",
        binding="handle",
        attributes={"has_close_method": True},
    )
    checker._binding_evidence.setdefault("handle", []).append(evidence)

    result = checker._verify_type_claim(
        {"type": "resource_type", "target": "handle", "expects_close": True}
    )
    assert result.status == VerificationStatus.VERIFIED


def test_resource_type_claim_abstains_without_evidence(tmp_path: Path):
    """Missing type evidence abstains instead of rejecting."""
    checker, _ = _make_cross_checker(tmp_path)
    result = checker._verify_type_claim(
        {"type": "resource_type", "target": "unknown_symbol", "expects_close": True}
    )
    assert result.status == VerificationStatus.UNVERIFIABLE


def test_proposal_with_rejected_type_claim_scores_low(tmp_path: Path):
    """End-to-end: a proposal claiming resp.File needs Close gets a low
    verification score because its resource_type claim is hallucinated."""
    checker, _ = _make_cross_checker(tmp_path)
    proposal = {
        "title": "关闭 resp.File",
        "location": "main/main.go:14",
        "problem": "resp.File 读取后未关闭",
        "evidence_claims": [
            {"type": "file_location", "target": "main/main.go:14"},
            {"type": "resource_type", "target": "resp.File", "expects_close": True},
        ],
    }
    verified = checker.verify_proposals([proposal])
    verification = verified[0]["__verification"]
    assert verification["verification_score"] <= 0.5
    flags_text = " ".join(verification.get("flags", []))
    assert "类型证据反驳" in flags_text
    assert verification["verdict"] == "partial"
