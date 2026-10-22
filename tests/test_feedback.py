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


def test_quick_feedback_ranking_is_annotated_and_lossless(tmp_path, monkeypatch):
    from io import StringIO
    from types import SimpleNamespace

    from rich.console import Console

    from smartbench.cli import phases

    events_path = tmp_path / "events.jsonl"
    store = FeedbackStore(events_path)
    for index in range(2):
        store.append(
            make_event(
                label="actionable",
                rule_id="r",
                language="go",
                confidence=0.8,
                reason=f"case {index}",
            )
        )
    suggestions = [
        {
            "rule_id": "other",
            "title": "other",
            "location": {"file_path": "b.go", "line_start": 2},
            "confidence": 0.9,
        },
        {
            "rule_id": "r",
            "title": "prioritized",
            "location": {"file_path": "a.go", "line_start": 1},
            "confidence": 0.6,
        },
    ]

    def fake_run(console, session, api_config, concern, **kwargs):
        kwargs.get("on_usage", lambda _: None)(
            {"model": "test", "prompt_tokens": 1, "completion_tokens": 1}
        )
        return SimpleNamespace(
            final_suggestions=[dict(item) for item in suggestions],
            analysis_report={},
        )

    monkeypatch.setattr(phases, "resolve_project_path", lambda console, project: tmp_path)
    monkeypatch.setattr(
        phases, "load_api_keys_from_env", lambda: {"models": [{"model": "test"}]}
    )
    monkeypatch.setattr(
        phases,
        "run_analysis_session",
        lambda console, path: SimpleNamespace(fingerprint=object()),
    )
    monkeypatch.setattr(phases, "display_fingerprint", lambda console, fingerprint: None)
    monkeypatch.setattr(phases, "run_diagnosis_with_session", fake_run)

    result = phases.run_quick_mode(
        Console(file=StringIO()),
        project=".",
        apply_feedback=True,
        feedback_file=str(events_path),
    )

    assert [item["rule_id"] for item in result.final_suggestions] == ["r", "other"]
    assert len(result.final_suggestions) == 2
    assert result.analysis_report["feedback"]["suppressed"] == 0
    assert result.final_suggestions[0]["feedback"]["support"] == 2
