"""Live blind orchestration never exposes target snapshots to ProjectReader."""

import json
from pathlib import Path

import pytest

from smartbench.experiments.project_reader_blind_online import (
    main,
    run_online_blind_project_reader_experiment,
)
from smartbench.graph.tree_parser import get_parser
from smartbench.llm.provider import ENV_PROVIDER_MAP

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


def _receiver_root(receiver: str) -> str:
    receiver = receiver.strip()
    while receiver.startswith(("&", "*", "(")):
        receiver = receiver[1:].lstrip()
    return receiver.split(".", 1)[0].split("(", 1)[0].strip()


def _grounded_reader(prompts: list[str]):
    def invoke(prompt: str, role: str = "") -> str:
        assert role == "project_reader"
        prompts.append(prompt)
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
                    and _receiver_root(
                        str(fact["attributes"].get("receiver", ""))
                    )
                    == binding
                ]
                if not cleanups:
                    continue
                cleanup_receiver = str(
                    cleanups[0]["attributes"].get("receiver", "")
                )
                member_path = cleanup_receiver[len(binding) :].lstrip(".")
                receiver_type = str(attributes.get("receiver_type", ""))
                canonical = attributes.get("canonical_receiver_symbols", [])
                type_ids = attributes.get("type_evidence_ids", [])
                mode = "exact"
                candidate = {
                    "candidate_id": f"blind-{len(candidates)}",
                    "operation_id": attributes["operation_id"],
                    "acquire_symbol": acquire["object"],
                    "resource_result_index": index,
                    "cleanup_methods": sorted(
                        {fact["object"].rsplit(".", 1)[-1] for fact in cleanups}
                    ),
                    "resource_member_path": member_path,
                    "confidence": 0.9,
                    "fact_ids": [
                        acquire["fact_id"],
                        *(fact["fact_id"] for fact in cleanups),
                    ],
                }
                if member_path and receiver_type and len(canonical) == 1 and type_ids:
                    mode = "typed_method"
                    candidate.update(
                        {
                            "receiver_type": receiver_type,
                            "canonical_acquire": canonical[0],
                            "type_evidence_ids": type_ids,
                        }
                    )
                elif member_path:
                    mode = "method_shape"
                candidate["acquire_match_mode"] = mode
                candidates.append(candidate)
        return json.dumps(
            {
                "architecture_summary": "Bounded blind reference inventory.",
                "components": [],
                "resource_candidates": candidates,
                "uncertainties": [],
            }
        )

    return invoke


def test_repeated_online_blind_trials_are_target_excluded_and_typed():
    root = Path(__file__).resolve().parents[1]
    prompts: list[str] = []
    report = run_online_blind_project_reader_experiment(
        root / "benchmarks" / "real" / "manifest.yaml",
        root / "benchmarks" / "experiments" / "project_reader_blind" / "manifest.yaml",
        _grounded_reader(prompts),
        negative_path=(
            root / "benchmarks" / "experiments" / "project_reader_resource" / "negative"
        ),
        trials=2,
    )
    encoded = report.to_dict()

    assert report.passed
    assert encoded["decision"] == "partial"
    assert encoded["summary"] == {
        "cases": 4,
        "reference_available": 2,
        "trials_executed": 4,
        "trials_passed": 4,
        "proposed_candidates": 4,
        "supported_protocols": 4,
        "rejected_candidates": 0,
        "initial_rejected_candidates": 0,
        "initially_accepted_trials": 4,
        "repair_attempts": 0,
        "recovered_trials": 0,
        "stable_detected_cases": 2,
        "stable_diagnostic_coverage": 0.5,
    }
    assert len(prompts) == 4
    assert all("retrieval/target.go" not in prompt for prompt in prompts)
    assert all("pkg/kubectl/resource_printer.go" not in prompt for prompt in prompts)
    assert encoded["privacy"]["target_snapshots_in_model_prompt"] is False
    assert report.independent_negative_findings == 0

    prometheus = next(
        case for case in report.cases if case.source_repository == "prometheus/prometheus"
    )
    assert prometheus.stable_detected
    assert all(
        trial.protocols[0]["acquire_match_mode"] == "typed_method"
        for trial in prometheus.trials
    )
    assert all(
        trial.finding_witnesses[0]["canonical_acquire"] == "net/http.Client.Do"
        for trial in prometheus.trials
    )

    unsupported = [case for case in report.cases if not case.reference_available]
    assert len(unsupported) == 2
    assert all(case.trials == () and case.passed for case in unsupported)


def test_evidence_feedback_repairs_rejected_cleanup_citations():
    root = Path(__file__).resolve().parents[1]
    prompts: list[str] = []
    grounded = _grounded_reader(prompts)
    calls = 0

    def initially_incomplete(prompt: str, role: str = "") -> str:
        nonlocal calls
        calls += 1
        output = json.loads(grounded(prompt, role))
        if calls % 2 == 1:
            for candidate in output["resource_candidates"]:
                candidate["fact_ids"] = candidate["fact_ids"][:1]
        return json.dumps(output)

    report = run_online_blind_project_reader_experiment(
        root / "benchmarks" / "real" / "manifest.yaml",
        root / "benchmarks" / "experiments" / "project_reader_blind" / "manifest.yaml",
        initially_incomplete,
        negative_path=(
            root / "benchmarks" / "experiments" / "project_reader_resource" / "negative"
        ),
        trials=1,
        max_repairs=1,
    )

    assert report.passed
    assert len(prompts) == 4
    trials = [trial for case in report.cases for trial in case.trials]
    assert len(trials) == 2
    assert all(trial.initial_rejected_candidates == 1 for trial in trials)
    assert all(trial.repair_attempts == 1 for trial in trials)
    assert all(trial.recovered_by_repair for trial in trials)
    assert all(trial.rejected_candidates == 0 and trial.passed for trial in trials)
    assert report.to_dict()["summary"]["recovered_trials"] == 2


def test_online_blind_cli_reports_missing_provider_without_leaking_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    for env_var in ENV_PROVIDER_MAP:
        monkeypatch.delenv(env_var, raising=False)
    output = tmp_path / "blind-online.json"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        "sys.argv",
        [
            "project-reader-blind-online",
            "--benchmark-manifest",
            str(root / "benchmarks" / "real" / "manifest.yaml"),
            "--blind-manifest",
            str(
                root
                / "benchmarks"
                / "experiments"
                / "project_reader_blind"
                / "manifest.yaml"
            ),
            "--output",
            str(output),
        ],
    )

    assert main() == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "unavailable"
    assert report["passed"] is False
    assert report["privacy"]["api_keys_persisted"] is False
