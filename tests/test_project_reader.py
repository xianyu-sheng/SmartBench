"""ProjectReader hypotheses stay outside the deterministic fact boundary."""

import json
from pathlib import Path

import pytest

from smartbench.analysis import AcquireMatchMode
from smartbench.core.adapters import GoAdapter
from smartbench.engine.project_reader import (
    CandidateSemanticMapping,
    DeterministicEvidenceResolver,
    EvidenceResolutionStatus,
    MappingStatus,
    ProjectModel,
    ProjectModelValidator,
    ProjectReaderAgent,
    build_project_inventory,
)
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


SOURCE = """
package sample

func load(path string) error {
    file, err := os.Open(path)
    if err != nil {
        return err
    }
    defer file.Close()
    return parse(file)
}
""".strip()


def _ir(tmp_path: Path):
    (tmp_path / "loader.go").write_text(SOURCE, encoding="utf-8")
    return GoAdapter().parse_semantic_project(tmp_path)


def _acquire_fact(inventory):
    return next(
        fact
        for fact in inventory.facts
        if fact.object == "os.Open" and fact.attributes.get("primary_result_call") is True
    )


def _cleanup_fact(inventory):
    return next(
        fact
        for fact in inventory.facts
        if fact.object == "file.Close"
        and fact.attributes.get("inventory_role") == "cleanup_registration"
    )


def test_project_reader_candidate_is_structurally_grounded(tmp_path: Path):
    ir = _ir(tmp_path)
    inventory = build_project_inventory(ir)
    fact = _acquire_fact(inventory)
    cleanup_fact = _cleanup_fact(inventory)
    output = {
        "architecture_summary": "The loader opens and parses a file.",
        "components": ["loader"],
        "resource_candidates": [
            {
                "candidate_id": "file-protocol",
                "operation_id": fact.attributes["operation_id"],
                "acquire_symbol": "os.Open",
                "resource_result_index": 0,
                "cleanup_methods": ["Close"],
                "confidence": 0.8,
                "fact_ids": [fact.fact_id, cleanup_fact.fact_id],
            }
        ],
        "uncertainties": ["Resource meaning is still a hypothesis."],
    }
    reader = ProjectReaderAgent(lambda _prompt, role="": json.dumps(output))
    result = reader.read(ir)

    assert result.error == ""
    assert result.model is not None
    validation = ProjectModelValidator().validate(ir, result.model, result.inventory)
    assert len(validation.protocols) == 1
    assert validation.decisions[0].status == MappingStatus.SUPPORTED
    assert validation.protocols[0].acquire_symbol == "os.Open"
    assert validation.protocols[0].evidence_fact_ids == (
        fact.fact_id,
        cleanup_fact.fact_id,
    )


def test_hallucinated_operation_is_rejected(tmp_path: Path):
    ir = _ir(tmp_path)
    inventory = build_project_inventory(ir)
    fact = _acquire_fact(inventory)
    model = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="invented",
                operation_id="does-not-exist",
                acquire_symbol="os.Open",
                resource_result_index=0,
                cleanup_methods=("Close",),
                confidence=0.9,
                fact_ids=(fact.fact_id, _cleanup_fact(inventory).fact_id),
            ),
        )
    )

    validation = ProjectModelValidator().validate(ir, model, inventory)
    assert validation.protocols == ()
    assert validation.decisions[0].status == MappingStatus.REJECTED
    assert "existing call" in validation.decisions[0].reason


def test_invented_cleanup_method_is_rejected(tmp_path: Path):
    ir = _ir(tmp_path)
    inventory = build_project_inventory(ir)
    fact = _acquire_fact(inventory)
    model = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="invented-cleanup",
                operation_id=str(fact.attributes["operation_id"]),
                acquire_symbol="os.Open",
                resource_result_index=0,
                cleanup_methods=("DestroyEverything",),
                confidence=0.9,
                fact_ids=(fact.fact_id, _cleanup_fact(inventory).fact_id),
            ),
        )
    )

    validation = ProjectModelValidator().validate(ir, model, inventory)
    assert validation.protocols == ()
    assert validation.decisions[0].status == MappingStatus.REJECTED
    assert "lack cited reachable registrations" in validation.decisions[0].reason


def test_missing_fact_id_is_rejected_even_for_real_operation(tmp_path: Path):
    ir = _ir(tmp_path)
    inventory = build_project_inventory(ir)
    fact = _acquire_fact(inventory)
    model = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="uncited",
                operation_id=str(fact.attributes["operation_id"]),
                acquire_symbol="os.Open",
                resource_result_index=0,
                cleanup_methods=("Close",),
                confidence=0.9,
                fact_ids=("fact-invented",),
            ),
        )
    )

    validation = ProjectModelValidator().validate(ir, model, inventory)
    assert validation.decisions[0].status == MappingStatus.REJECTED
    assert "missing inventory facts" in validation.decisions[0].reason


def test_resolver_owns_opaque_fact_ids_and_preserves_agent_audit(tmp_path: Path):
    ir = _ir(tmp_path)
    inventory = build_project_inventory(ir)
    fact = _acquire_fact(inventory)
    model = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="resolver-owned",
                operation_id=str(fact.attributes["operation_id"]),
                acquire_symbol="os.Open",
                resource_result_index=0,
                cleanup_methods=("Close",),
                confidence=0.9,
                fact_ids=("fact-invented-by-agent",),
            ),
        )
    )

    resolution = DeterministicEvidenceResolver().resolve(ir, model, inventory)

    assert resolution.decisions[0].status == EvidenceResolutionStatus.RESOLVED
    assert resolution.decisions[0].agent_fact_ids == ("fact-invented-by-agent",)
    assert resolution.decisions[0].resolved_fact_ids == (
        fact.fact_id,
        _cleanup_fact(inventory).fact_id,
    )
    validation = ProjectModelValidator().validate(ir, resolution.model, inventory)
    assert len(validation.protocols) == 1
    assert validation.protocols[0].evidence_fact_ids == (
        fact.fact_id,
        _cleanup_fact(inventory).fact_id,
    )


def test_resolver_abstains_when_cleanup_evidence_is_ambiguous(tmp_path: Path):
    source = SOURCE.replace("defer file.Close()", "defer file.Close()\n    defer file.Close()")
    (tmp_path / "loader.go").write_text(source, encoding="utf-8")
    ir = GoAdapter().parse_semantic_project(tmp_path)
    inventory = build_project_inventory(ir)
    fact = _acquire_fact(inventory)
    model = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="ambiguous-cleanup",
                operation_id=str(fact.attributes["operation_id"]),
                acquire_symbol="os.Open",
                resource_result_index=0,
                cleanup_methods=("Close",),
                confidence=0.9,
            ),
        )
    )

    resolution = DeterministicEvidenceResolver().resolve(ir, model, inventory)

    assert resolution.model.resource_candidates == ()
    assert resolution.decisions[0].status == EvidenceResolutionStatus.AMBIGUOUS
    assert "2 structural matches" in resolution.decisions[0].reason


def test_reader_rejects_schema_expansion(tmp_path: Path):
    ir = _ir(tmp_path)
    reader = ProjectReaderAgent(
        lambda _prompt: json.dumps(
            {
                "architecture_summary": "summary",
                "components": [],
                "resource_candidates": [],
                "uncertainties": [],
                "invented_facts": ["trust me"],
            }
        )
    )

    result = reader.read(ir)
    assert result.model is None
    assert "unknown project model fields" in result.error


def test_typed_method_mapping_requires_grounded_type_and_member_cleanup(tmp_path: Path):
    source = """
package sample

func load(client *Client, req *Request) error {
    response, err := client.Do(req)
    if err != nil { return err }
    defer response.Body.Close()
    return decode(response.Body)
}
""".strip()
    (tmp_path / "http.go").write_text(source, encoding="utf-8")
    ir = GoAdapter().parse_semantic_project(tmp_path)
    inventory = build_project_inventory(ir)
    acquire = next(
        fact
        for fact in inventory.facts
        if fact.object == "client.Do" and fact.attributes.get("primary_result_call") is True
    )
    cleanup = next(fact for fact in inventory.facts if fact.object == "response.Body.Close")
    output = {
        "architecture_summary": "HTTP response resource protocol.",
        "components": ["client"],
        "resource_candidates": [
            {
                "candidate_id": "http-body",
                "operation_id": acquire.attributes["operation_id"],
                "acquire_symbol": "client.Do",
                "resource_result_index": 0,
                "cleanup_methods": ["Close"],
                "acquire_match_mode": "typed_method",
                "resource_member_path": "Body",
                "receiver_type": acquire.attributes["receiver_type"],
                "canonical_acquire": acquire.attributes["canonical_receiver_symbols"][0],
                "type_evidence_ids": acquire.attributes["type_evidence_ids"],
                "confidence": 0.8,
                "fact_ids": [acquire.fact_id, cleanup.fact_id],
            }
        ],
        "uncertainties": ["Receiver type remains unresolved."],
    }

    result = ProjectReaderAgent(lambda _prompt, role="": json.dumps(output)).read(ir)
    assert result.model is not None
    validation = ProjectModelValidator().validate(ir, result.model, result.inventory)
    assert len(validation.protocols) == 1
    assert validation.protocols[0].acquire_match_mode == AcquireMatchMode.TYPED_METHOD
    assert validation.protocols[0].receiver_type == "Client"
    assert validation.protocols[0].canonical_acquire == "Client.Do"
    assert validation.protocols[0].type_evidence_ids
    assert validation.protocols[0].resource_member_path == "Body"

    wrong = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="wrong-member",
                operation_id=str(acquire.attributes["operation_id"]),
                acquire_symbol="client.Do",
                resource_result_index=0,
                cleanup_methods=("Close",),
                confidence=0.8,
                fact_ids=(acquire.fact_id, cleanup.fact_id),
                acquire_match_mode=AcquireMatchMode.METHOD_SHAPE,
                resource_member_path="Payload",
            ),
        )
    )
    rejected = ProjectModelValidator().validate(ir, wrong, inventory)
    assert rejected.protocols == ()
    assert rejected.decisions[0].status == MappingStatus.REJECTED

    invented_type = ProjectModel(
        resource_candidates=(
            CandidateSemanticMapping(
                candidate_id="invented-type",
                operation_id=str(acquire.attributes["operation_id"]),
                acquire_symbol="client.Do",
                resource_result_index=0,
                cleanup_methods=("Close",),
                confidence=0.8,
                fact_ids=(acquire.fact_id, cleanup.fact_id),
                acquire_match_mode=AcquireMatchMode.TYPED_METHOD,
                resource_member_path="Body",
                receiver_type="OtherClient",
                canonical_acquire="OtherClient.Do",
                type_evidence_ids=tuple(acquire.attributes["type_evidence_ids"]),
            ),
        )
    )
    rejected_type = ProjectModelValidator().validate(ir, invented_type, inventory)
    assert rejected_type.protocols == ()
    assert "receiver type is not grounded" in rejected_type.decisions[0].reason
