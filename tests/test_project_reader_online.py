"""Online ProjectReader experiment orchestration and secret boundaries."""

import json
from pathlib import Path

import pytest

from smartbench.experiments.project_reader_online import (
    main,
    run_online_project_reader_experiment,
    sanitized_model_descriptors,
)
from smartbench.graph.tree_parser import get_parser
from smartbench.llm.provider import ENV_PROVIDER_MAP


def _receiver_root(receiver: str) -> str:
    receiver = receiver.strip()
    while receiver.startswith(("&", "*", "(")):
        receiver = receiver[1:].lstrip()
    return receiver.split(".", 1)[0].split("(", 1)[0].strip()


def _inventory_protocol_reader(prompt: str, role: str = "") -> str:
    assert role == "project_reader"
    serialized = prompt.split("<untrusted_project_inventory>\n", 1)[1].split(
        "\n</untrusted_project_inventory>", 1
    )[0]
    facts = json.loads(serialized)["facts"]
    candidates = []
    for acquire in facts:
        attributes = acquire["attributes"]
        if (
            attributes.get("inventory_role") != "result_call"
            or attributes.get("primary_result_call") is not True
        ):
            continue
        for index, binding in enumerate(attributes.get("result_targets", [])):
            cleanups = [
                fact
                for fact in facts
                if fact["subject"] == acquire["subject"]
                and fact["attributes"].get("inventory_role")
                == "cleanup_registration"
                and _receiver_root(str(fact["attributes"].get("receiver", "")))
                == binding
            ]
            if not cleanups:
                continue
            candidates.append(
                {
                    "candidate_id": f"inventory-{len(candidates)}",
                    "operation_id": attributes["operation_id"],
                    "acquire_symbol": acquire["object"],
                    "resource_result_index": index,
                    "cleanup_methods": sorted(
                        {fact["object"].rsplit(".", 1)[-1] for fact in cleanups}
                    ),
                    "confidence": 0.8,
                    "fact_ids": [
                        acquire["fact_id"],
                        *(fact["fact_id"] for fact in cleanups),
                    ],
                }
            )
    return json.dumps(
        {
            "architecture_summary": "Inventory-backed resource protocols.",
            "components": [],
            "resource_candidates": candidates[:30],
            "uncertainties": [],
        }
    )


@pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")
def test_online_orchestrator_applies_only_validated_model_protocols():
    root = Path(__file__).resolve().parents[1]
    report = run_online_project_reader_experiment(
        root / "benchmarks" / "real" / "manifest.yaml",
        _inventory_protocol_reader,
        negative_path=(
            root / "benchmarks" / "experiments" / "project_reader_resource" / "negative"
        ),
    )

    assert report.passed
    assert len(report.cases) == 4
    assert sum(case.proposed_candidates for case in report.cases) == 8
    assert sum(case.supported_protocols for case in report.cases) == 8
    assert sum(case.reference_protocol_matches for case in report.cases) == 8
    assert all(case.before_findings == 1 for case in report.cases)
    assert all(case.after_findings == 0 for case in report.cases)
    assert report.independent_negative_findings == 0


def test_model_descriptors_never_serialize_credentials():
    config = {
        "models": [
            {
                "provider": "example",
                "model": "model-name",
                "api_key": "must-not-escape",
                "base_url": "https://example.invalid",
            }
        ]
    }

    descriptors = sanitized_model_descriptors(config)
    encoded = json.dumps([item.to_dict() for item in descriptors])

    assert encoded == '[{"provider": "example", "model": "model-name"}]'
    assert "must-not-escape" not in encoded


def test_cli_writes_unavailable_report_without_supported_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    for env_var in ENV_PROVIDER_MAP:
        monkeypatch.delenv(env_var, raising=False)
    output = tmp_path / "online.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "project-reader-online",
            "--manifest",
            "benchmarks/real/manifest.yaml",
            "--output",
            str(output),
        ],
    )

    assert main() == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "unavailable"
    assert report["passed"] is False
    assert report["models"] == []
    assert report["privacy"]["api_keys_persisted"] is False
