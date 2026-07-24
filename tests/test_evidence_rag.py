"""Deterministic graph retrieval and evidence-pack contracts."""

from pathlib import Path

import pytest

from smartbench.detector.fingerprint import ProjectFingerprint
from smartbench.engine.debate import DebateEngine, EvidencePolicy
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.graph.schema import CodeEdge, CodeGraph, CodeNode, EdgeType, NodeType
from smartbench.ir import EvidencePack, EvidenceRef, FactKind, SemanticFact, SemanticIR


def _sample_ir(tmp_path: Path) -> SemanticIR:
    source = tmp_path / "app.py"
    source.write_text(
        "def entry():\n    helper()\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    graph = CodeGraph(meta={"project_path": str(tmp_path)})
    graph.add_node(CodeNode("entry", NodeType.FUNCTION, "entry", "app.py", 1, 2, "python"))
    graph.add_node(CodeNode("helper", NodeType.FUNCTION, "helper", "app.py", 4, 5, "python"))
    graph.add_edge(CodeEdge("entry", "helper", EdgeType.CALLS))
    return SemanticIR.from_graph(graph, language="python", project_path=str(tmp_path))


def test_graph_rag_is_stable_and_source_backed(tmp_path: Path):
    ir = _sample_ir(tmp_path)
    rag = DeterministicGraphRAG(ir)

    first = rag.retrieve("entry", hops=1, max_nodes=5)
    second = rag.retrieve("entry", hops=1, max_nodes=5)

    assert first.to_dict() == second.to_dict()
    assert first.graph_version
    assert first.facts
    assert first.evidence
    assert any(ref.file_path == "app.py" and ref.snippet for ref in first.evidence)
    assert "DETERMINISTIC" not in rag.render(first)


def test_debate_evidence_pack_is_explicitly_bounded():
    pack = EvidencePack(query="entry", graph_version="abc", retrieval_trace=("seed:x",))
    context = DebateEngine._append_evidence_pack("base context", pack)

    assert "base context" in context
    assert "DETERMINISTIC EVIDENCE PACK" in context
    assert "graph_version" in context
    assert "mark it as unknown" in context


def test_debate_receives_the_same_evidence_contract(tmp_path: Path):
    prompts: list[str] = []
    responses = iter([
        '{"proposals": []}',
        '{"verdicts": []}',
        '{"final_suggestions": []}',
    ])

    def llm(prompt: str, **_kwargs: object) -> str:
        prompts.append(prompt)
        return next(responses)

    engine = DebateEngine(
        llm,
        fingerprint=ProjectFingerprint(
            project_path=tmp_path,
            project_name="fixture",
        ),
        max_call_attempts=1,
    )
    pack = EvidencePack(query="entry", graph_version="stable-v1")
    result = engine.debate("base", evidence_pack=pack)

    assert result.consensus_reached is True
    assert result.debate_log[0]["role"] == "evidence"
    assert all("DETERMINISTIC EVIDENCE PACK" in prompt for prompt in prompts)


def test_exclusive_debate_rejects_context_and_unsupported_suggestions(tmp_path: Path):
    fact = SemanticFact(
        subject="app.py",
        predicate=FactKind.STATE_TRANSITION,
        object="retry without terminal guard",
        evidence=(EvidenceRef("app.py", 4, snippet="retries += 1"),),
    )
    pack = EvidencePack.from_facts("retry", [fact], graph_version="stable-v2")
    prompts: list[str] = []
    responses = iter([
        '{"proposals": ['
        f'{{"title": "grounded", "fact_ids": ["{fact.fact_id}"]}},'
        '{"title": "invented", "fact_ids": ["fact-does-not-exist"]}'
        "]}",
        '{"verdicts": []}',
        '{"final_suggestions": ['
        f'{{"title": "grounded", "fact_ids": ["{fact.fact_id}"]}},'
        '{"title": "invented", "fact_ids": []}'
        "]}",
    ])

    def llm(prompt: str, **_kwargs: object) -> str:
        prompts.append(prompt)
        return next(responses)

    engine = DebateEngine(
        llm,
        fingerprint=ProjectFingerprint(project_path=tmp_path, project_name="fixture"),
        max_call_attempts=1,
        evidence_policy=EvidencePolicy.EXCLUSIVE,
    )
    result = engine.debate("RAW_CONTEXT_MUST_NOT_LEAK", evidence_pack=pack)

    assert [item["title"] for item in result.final_suggestions] == ["grounded"]
    assert all("RAW_CONTEXT_MUST_NOT_LEAK" not in prompt for prompt in prompts)
    assert all(fact.fact_id in prompt for prompt in prompts)
    gates = [entry for entry in result.debate_log if entry["role"] == "evidence_gate"]
    assert [(entry["stage"], entry["rejected"]) for entry in gates] == [
        ("proposer", 1),
        ("final", 1),
    ]


def test_required_evidence_policy_rejects_missing_pack(tmp_path: Path):
    engine = DebateEngine(
        lambda _prompt: "{}",
        fingerprint=ProjectFingerprint(project_path=tmp_path, project_name="fixture"),
        evidence_policy=EvidencePolicy.REQUIRED,
    )

    with pytest.raises(ValueError, match="evidence_pack is required"):
        engine.debate("context")
