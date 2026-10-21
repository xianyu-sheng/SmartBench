"""Offline tests for trial stability, usage capture and cost estimates (issue #12)."""

from smartbench.llm.client import call_llm
from smartbench.llm.pricing import estimate_cost
from smartbench.llm.trials import (
    TrialRecord,
    UsageAccumulator,
    suggestion_signature,
    summarize_trials,
)


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""
        self.headers = {}

    def json(self):
        return self._payload


def _config(provider="deepseek", model="test-model"):
    base_url = (
        "https://api.anthropic.com/v1"
        if provider == "anthropic"
        else "https://example.test/v1"
    )
    return {
        "models": [
            {
                "provider": provider,
                "model": model,
                "api_key": "secret-key",
                "base_url": base_url,
                "role": "all",
            }
        ]
    }


def _record(index, signature, *, suggestions=2, prompt=10, completion=5):
    return TrialRecord(
        index=index,
        suggestions=suggestions,
        signature=signature,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cost_usd=0.001,
        duration_ms=100,
    )


def test_suggestion_signature_is_order_independent():
    first = [
        {"rule_id": "r", "location": {"file_path": "a.py", "line_start": 3}, "title": "x"},
        {"rule_id": "s", "location": "b.py:7", "title": "y"},
    ]
    reordered = list(reversed(first))
    changed = [dict(first[0], title="z"), first[1]]

    assert suggestion_signature(first) == suggestion_signature(reordered)
    assert suggestion_signature(first) != suggestion_signature(changed)


def test_trial_summary_surfaces_disagreement():
    report = summarize_trials(
        [_record(1, "aaa"), _record(2, "aaa"), _record(3, "bbb")],
        temperature=0.3,
        seed=42,
        model="deepseek-chat",
    )

    assert report.stable is False
    assert report.majority_count == 2
    assert report.stability_rate == 0.6667
    assert report.total_tokens == 45
    assert report.total_cost_usd == 0.003
    assert report.to_dict()["trials"][2]["signature"] == "bbb"


def test_trial_summary_all_agree():
    report = summarize_trials(
        [_record(1, "same"), _record(2, "same")],
        temperature=0.0,
        seed=7,
        model="test-model",
    )

    assert report.stable is True
    assert report.stability_rate == 1.0
    assert report.majority_count == 2


def test_usage_accumulator_sums_calls():
    usage = UsageAccumulator()
    usage.add({"model": "m", "prompt_tokens": 10, "completion_tokens": 5})
    usage.add({"model": "m", "prompt_tokens": 2, "completion_tokens": 1})

    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 6
    assert usage.total_tokens == 18
    assert usage.model == "m"
    assert usage.calls == 2


def test_cost_estimation(monkeypatch):
    assert estimate_cost("deepseek-chat", 1_000_000, 1_000_000) == 1.37
    assert estimate_cost("unknown-model", 1000, 1000) is None
    assert estimate_cost(None, 1000, 1000) is None

    monkeypatch.setenv("SMARTBENCH_PRICE_INPUT_PER_M", "1")
    monkeypatch.setenv("SMARTBENCH_PRICE_OUTPUT_PER_M", "2")
    assert estimate_cost("unknown-model", 1_000_000, 1_000_000) == 3.0


def test_call_llm_reports_usage_and_settings(monkeypatch):
    captured = {}
    usages = []

    def fake_post(url, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse(
            {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    result = call_llm(
        _config(),
        "inspect this",
        temperature=0.1,
        seed=42,
        on_usage=usages.append,
    )

    assert result == '{"ok": true}'
    assert captured["temperature"] == 0.1
    assert captured["seed"] == 42
    assert usages == [
        {
            "model": "test-model",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    ]


def test_anthropic_usage_mapping(monkeypatch):
    captured = {}
    usages = []

    def fake_post(url, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse(
            {
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            }
        )

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    call_llm(
        _config(provider="anthropic"),
        "inspect this",
        temperature=0.0,
        seed=42,
        on_usage=usages.append,
    )

    assert captured["temperature"] == 0.0
    assert "seed" not in captured
    assert usages == [
        {
            "model": "test-model",
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        }
    ]


def test_quick_mode_trials_report_stability(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace

    from rich.console import Console

    from smartbench.cli import phases

    calls = []

    def fake_run(console, session, api_config, concern, **kwargs):
        calls.append(kwargs)
        kwargs["on_usage"](
            {"model": "deepseek-chat", "prompt_tokens": 10, "completion_tokens": 5}
        )
        title = "same" if len(calls) == 1 else "different"
        return SimpleNamespace(
            final_suggestions=[
                {"rule_id": "r", "location": {"file_path": "a.py", "line_start": 1}, "title": title}
            ],
            analysis_report={},
        )

    monkeypatch.setattr(phases, "resolve_project_path", lambda console, project: object())
    monkeypatch.setattr(phases, "load_api_keys_from_env", lambda: {"models": [{"model": "deepseek-chat"}]})
    monkeypatch.setattr(phases, "run_analysis_session", lambda console, path: SimpleNamespace(fingerprint=object()))
    monkeypatch.setattr(phases, "display_fingerprint", lambda console, fingerprint: None)
    monkeypatch.setattr(phases, "run_diagnosis_with_session", fake_run)

    result = phases.run_quick_mode(
        Console(file=StringIO()),
        project=".",
        trials=2,
        temperature=0.2,
        seed=7,
    )

    report = result.analysis_report["trials"]
    assert report["count"] == 2
    assert report["stable"] is False
    assert report["stability_rate"] == 0.5
    assert report["total_tokens"] == 30
    assert report["total_cost_usd"] == 0.000016
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["seed"] == 7
