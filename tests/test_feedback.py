"""Tests for feedback events, storage and offline ranking (issue #45, phase 1)."""

import json

import pytest
from typer.testing import CliRunner

from smartbench.cli.main import app
from smartbench.feedback import (
    FeedbackStore,
    build_priors,
    make_event,
    rank_findings,
    suggest_thresholds,
    validate_event,
)


def _event(label, *, rule="r", language="python", confidence=None, reason=""):
    return make_event(
        label=label,
        rule_id=rule,
        language=language,
        confidence=confidence,
        reason=reason,
    )


def test_make_event_and_validation():
    event = _event("actionable")

    assert validate_event(event.to_dict()) == []
    with pytest.raises(ValueError):
        make_event(label="not-a-label")
    invalid = event.to_dict()
    invalid["confidence"] = 1.5
    assert any("confidence" in error for error in validate_event(invalid))


def test_store_deduplicates_and_filters(tmp_path):
    store = FeedbackStore(tmp_path / "events.jsonl")
    first = _event("actionable")
    second = _event("intentional", reason="by design")

    assert store.append(first) is True
    assert store.append(second) is True
    assert store.append(first) is False
    assert len(store.events()) == 2
    assert len(store.filter(label="actionable")) == 1
    assert store.counts() == {"actionable": 1, "intentional": 1}


def test_rank_orders_without_dropping():
    events = [
        _event("actionable"),
        _event("actionable"),
        _event("intentional"),
    ]
    findings = [
        {"rule_id": "other", "file_path": "b.py", "line_start": 2, "confidence": 0.99},
        {"rule_id": "r", "file_path": "a.py", "line_start": 1, "confidence": 0.8},
    ]

    payload = rank_findings(findings, events)

    assert [item["rule_id"] for item in payload["findings"]] == ["r", "other"]
    assert payload["policy"]["ranked"] == 2
    assert payload["policy"]["suppressed"] == 0
    assert payload["findings"][0]["feedback"]["support"] == 3
    assert payload["findings"][1]["feedback"]["support"] == 0


def test_threshold_suggestion_is_advisory():
    events = [
        _event("actionable", confidence=0.9),
        _event("intentional", confidence=0.6),
        _event("intentional", confidence=0.7),
    ]

    suggestions = suggest_thresholds(events)

    assert len(suggestions) == 1
    assert suggestions[0]["suggested_min_confidence"] == 0.7
    assert "never suppressed" in suggestions[0]["note"]
    prior = build_priors(events)[("r", "python")]
    assert prior.support == 3
    assert prior.actionable == 1
    assert prior.actionable_ratio == 0.4


def test_cli_add_list_rank(tmp_path):
    store_path = tmp_path / "events.jsonl"
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "rule_id": "r",
                        "location": {"file_path": "a.py", "line_start": 1},
                        "confidence": 0.8,
                    },
                    {
                        "rule_id": "other",
                        "location": {"file_path": "b.py", "line_start": 2},
                        "confidence": 0.9,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    for index, label in enumerate(("actionable", "actionable", "intentional")):
        added = runner.invoke(
            app,
            [
                "feedback",
                "add",
                "--label",
                label,
                "--rule-id",
                "r",
                "--language",
                "python",
                "--reason",
                f"case {index}",
                "--file",
                str(store_path),
            ],
        )
        assert added.exit_code == 0, added.output

    listed = runner.invoke(
        app, ["feedback", "list", "--file", str(store_path), "--json"]
    )
    assert listed.exit_code == 0, listed.output
    assert len(json.loads(listed.output)["events"]) == 3

    ranked = runner.invoke(
        app,
        [
            "feedback",
            "rank",
            "--input",
            str(report),
            "--file",
            str(store_path),
            "--json",
        ],
    )
    assert ranked.exit_code == 0, ranked.output
    payload = json.loads(ranked.output)
    assert len(payload["findings"]) == 2
    assert payload["findings"][0]["rule_id"] == "r"
    assert payload["policy"]["suppressed"] == 0
