"""Deterministic, offline demonstration of the ProjectReader evidence loop.

This is a mechanism demo, not an LLM quality benchmark.  A scripted hypothesis
agent intentionally emits one invalid cleanup selector, consumes deterministic
rejection feedback, and returns a corrected semantic candidate.  The unchanged
resolver, validator and CFG analyzer then produce the real witness.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartbench.experiments.project_reader_blind_online import (
    run_online_blind_project_reader_experiment,
)


def run_demo(root: Path):
    project_root = root.expanduser().resolve()
    return run_online_blind_project_reader_experiment(
        project_root / "benchmarks" / "real" / "manifest.yaml",
        project_root / "benchmarks" / "experiments" / "project_reader_blind" / "manifest.yaml",
        _scripted_hypothesis_agent,
        negative_path=(
            project_root / "benchmarks" / "experiments" / "project_reader_resource" / "negative"
        ),
        trials=1,
        max_repairs=1,
    )


def _scripted_hypothesis_agent(prompt: str, role: str = "") -> str:
    if role != "project_reader":
        raise ValueError("demo agent only accepts the project_reader role")
    serialized = prompt.split("<untrusted_project_inventory>\n", 1)[1].split(
        "\n</untrusted_project_inventory>", 1
    )[0]
    facts = json.loads(serialized)["facts"]
    repairing = "<deterministic_validation_feedback>" in prompt
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
                and fact["attributes"].get("inventory_role") == "cleanup_registration"
                and _receiver_root(str(fact["attributes"].get("receiver", ""))) == binding
            ]
            if not cleanups:
                continue
            cleanup_receiver = str(cleanups[0]["attributes"].get("receiver", ""))
            member_path = cleanup_receiver[len(binding) :].lstrip(".")
            cleanup_methods = sorted({fact["object"].rsplit(".", 1)[-1] for fact in cleanups})
            receiver_type = str(attributes.get("receiver_type", ""))
            canonical = attributes.get("canonical_receiver_symbols", [])
            type_ids = attributes.get("type_evidence_ids", [])
            mode = "exact"
            candidate = {
                "candidate_id": f"demo-{len(candidates)}",
                "operation_id": attributes["operation_id"],
                "acquire_symbol": acquire["object"],
                "resource_result_index": index,
                "cleanup_methods": cleanup_methods if repairing else ["Release"],
                "resource_member_path": member_path,
                "confidence": 0.9,
            }
            if member_path and receiver_type and len(canonical) == 1 and type_ids:
                mode = "typed_method"
                candidate.update(
                    {
                        "receiver_type": receiver_type,
                        "canonical_acquire": canonical[0],
                    }
                )
            elif member_path:
                mode = "method_shape"
            candidate["acquire_match_mode"] = mode
            candidates.append(candidate)
    return json.dumps(
        {
            "architecture_summary": "Scripted hypothesis for an offline trust-boundary demo.",
            "components": [],
            "resource_candidates": candidates,
            "uncertainties": ([] if repairing else ["Cleanup method is intentionally wrong."]),
        }
    )


def _receiver_root(receiver: str) -> str:
    value = receiver.strip()
    while value.startswith(("&", "*", "(")):
        value = value[1:].lstrip()
    return value.split(".", 1)[0].split("(", 1)[0].strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline evidence-loop demo.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_demo(args.project_root)
    if args.output is not None:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    trials = [trial for case in report.cases for trial in case.trials]
    print(
        json.dumps(
            {
                "demo_type": "offline-scripted-agent-mechanism-demo",
                "passed": report.passed,
                "cases": [
                    {
                        "case": case.case_id,
                        "gate_rejected_initial_hypothesis": bool(
                            case.trials[0].initial_rejected_candidates
                        ),
                        "repair_attempts": case.trials[0].repair_attempts,
                        "evidence_resolution": case.trials[0].evidence_resolution[0]["status"],
                        "validator": case.trials[0].decisions[0].status,
                        "cfg_witness": case.trials[0].finding_witnesses[0]["proof"],
                        "before_findings": case.trials[0].before_findings,
                        "after_findings": case.trials[0].after_findings,
                        "negative_findings": case.trials[0].negative_findings,
                    }
                    for case in report.cases
                    if case.trials
                ],
                "summary": {
                    "trials": len(trials),
                    "recovered_by_bounded_repair": sum(
                        trial.recovered_by_repair for trial in trials
                    ),
                },
                "full_report": str(args.output.resolve()) if args.output else "",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
