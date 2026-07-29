"""Language-neutral type evidence is stable, strict and provenance-bearing."""

from smartbench.graph.schema import CodeGraph
from smartbench.ir import (
    EvidenceRef,
    SemanticIR,
    TypeEvidence,
    TypeEvidenceIndex,
    TypeEvidenceRole,
    TypeEvidenceSource,
    normalize_type_name,
    type_names_compatible,
)


def _item(type_name: str = "*net/http.Client") -> TypeEvidence:
    return TypeEvidence(
        operation_id="operation-1",
        role=TypeEvidenceRole.RECEIVER,
        type_name=type_name,
        source=TypeEvidenceSource.SURFACE_FIELD,
        provider="go.surface",
        binding="client",
        canonical_symbol="net/http.Client.Do",
        evidence=(
            EvidenceRef(
                file_path="client.go",
                line_start=12,
                snippet="client.Do(req)",
                source="go_type_evidence",
            ),
        ),
        attributes={"field_path": "httpClient"},
    )


def test_type_evidence_id_is_stable_and_normalizes_pointer_spelling():
    first = _item()
    second = _item(" & net/http.Client ")

    assert first.normalized_type == "net/http.Client"
    assert first.evidence_id == second.evidence_id
    assert first.to_dict()["evidence"][0]["line_start"] == 12


def test_type_compatibility_requires_exact_normalized_identity():
    assert normalize_type_name(" **http.Client ") == "http.Client"
    assert type_names_compatible("*net/http.Client", "net/http.Client")
    assert not type_names_compatible("net/http.Client", "custom/http.Client")
    assert not type_names_compatible("Client", "net/http.Client")
    assert not type_names_compatible("", "net/http.Client")


def test_type_evidence_index_abstains_on_ambiguity():
    unambiguous = TypeEvidenceIndex([_item(), _item()])
    ambiguous = TypeEvidenceIndex([_item(), _item("custom.Client")])

    assert (
        unambiguous.unique_type("operation-1", TypeEvidenceRole.RECEIVER)
        == "net/http.Client"
    )
    assert unambiguous.canonical_symbols("operation-1") == ("net/http.Client.Do",)
    assert ambiguous.unique_type("operation-1", TypeEvidenceRole.RECEIVER) == ""


def test_semantic_ir_merge_deduplicates_and_serializes_type_evidence():
    item = _item()
    left = SemanticIR(graph=CodeGraph(), type_evidence=[item])
    right = SemanticIR(graph=CodeGraph(), type_evidence=[item])

    merged = left.merge(right)
    encoded = merged.to_dict()

    assert merged.type_evidence == [item]
    assert encoded["type_evidence"][0]["evidence_id"] == item.evidence_id
    assert encoded["type_evidence"][0]["evidence"][0]["snippet"] == "client.Do(req)"
