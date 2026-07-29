"""The shared session keeps deterministic and Agent stages on one SemanticIR."""

import io
import json
import re

import pytest
from rich.console import Console

from smartbench.cli import phases
from smartbench.core import AnalysisSession, UnifiedDiagnosticConfig
from smartbench.graph.tree_parser import get_parser


def test_session_builds_complete_semantic_ir_once(tmp_path):
    (tmp_path / "app.py").write_text(
        "def load(value: str):\n"
        "    result = normalize(value)\n"
        "    return result\n",
        encoding="utf-8",
    )

    session = AnalysisSession.analyze(
        tmp_path,
        UnifiedDiagnosticConfig(languages=["python"]),
    )

    assert session.ir is not None
    assert session.ir.languages == ("python",)
    assert session.ir.operations
    assert "semantic_linker" in session.ir.meta
    pack = session.build_evidence_pack("load normalize")
    assert pack.graph_version
    assert pack.retrieval_trace[0] == "analysis-session:semantic-ir"


def test_quick_without_provider_returns_the_shared_session_report(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "app.py").write_text(
        "def load(value: str):\n"
        "    result = normalize(value)\n"
        "    return result\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(phases, "load_api_keys_from_env", lambda: None)
    output = io.StringIO()

    result = phases.run_quick_mode(
        Console(file=output, width=100),
        project=str(tmp_path),
        concern="inspect load",
    )

    assert result is not None
    assert result.pipeline["session"] == "analysis-session/v1"
    assert result.pipeline["shared_semantic_ir"] is True
    assert result.ir is not None
    assert result.ir.operations
    assert "semantic_linker" in result.ir.meta
    assert "AnalysisSession" in output.getvalue()


@pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")
def test_project_reader_and_rules_share_session_ir(tmp_path):
    (tmp_path / "main.go").write_text(
        "package main\n\n"
        "func safe() {\n"
        "    response, err := client.Do(request)\n"
        "    if err != nil { return }\n"
        "    defer response.Body.Close()\n"
        "    consume(response.Body)\n"
        "}\n\n"
        "func unsafe() {\n"
        "    response, err := client.Do(request)\n"
        "    if err != nil { return }\n"
        "    consume(response.Body)\n"
        "}\n",
        encoding="utf-8",
    )
    session = AnalysisSession.analyze(
        tmp_path,
        UnifiedDiagnosticConfig(languages=["go"]),
    )
    assert session.ir is not None
    assert session.ir.operations, session.result.errors

    def project_reader_response(prompt, role=""):
        assert role == "project_reader"
        serialized = re.search(
            r"<untrusted_project_inventory>\n(.*?)\n</untrusted_project_inventory>",
            prompt,
            re.DOTALL,
        ).group(1)
        inventory = json.loads(serialized)
        result_call = next(
            fact
            for fact in inventory["facts"]
            if fact["attributes"].get("inventory_role") == "result_call"
            and fact["attributes"].get("primary_result_call") is True
        )
        return json.dumps(
            {
                "architecture_summary": "HTTP response lifecycle",
                "components": ["main"],
                "resource_candidates": [
                    {
                        "candidate_id": "http-response",
                        "operation_id": result_call["attributes"]["operation_id"],
                        "acquire_symbol": result_call["object"],
                        "resource_result_index": 0,
                        "cleanup_methods": ["Close"],
                        "acquire_match_mode": "exact",
                        "resource_member_path": "Body",
                        "receiver_type": "",
                        "canonical_acquire": "",
                        "confidence": 0.9,
                    }
                ],
                "uncertainties": [],
            }
        )

    stage = session.run_project_reader(project_reader_response, max_repairs=0)

    assert stage.status == "findings"
    assert len(stage.validation.protocols) == 1
    assert len(stage.findings) == 1
    assert stage.findings[0].location.file_path == "main.go"
    assert stage.facts[0].attributes["proof"] == "cfg_dominance_between_acquire_and_use"
    assert any(
        finding.rule_id == "project_resource_lifecycle"
        for finding in session.result.findings
    )
    pack = session.build_evidence_pack("response body cleanup")
    assert stage.facts[0].fact_id in {fact.fact_id for fact in pack.facts}
    assert any(
        hypothesis.kind == "project_resource_protocol"
        for hypothesis in pack.hypotheses
    )
    assert all(
        not hasattr(hypothesis, "fact_id")
        for hypothesis in pack.hypotheses
    )
    assert session.report_dict()["pipeline"]["shared_semantic_ir"] is True
