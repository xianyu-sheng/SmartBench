"""Protocol and reliability tests for the LLM integration layer."""

import json
from io import StringIO

from rich.console import Console

import smartbench.llm.provider as provider_module
from smartbench.llm.client import call_llm, parse_json_safe
from smartbench.llm.provider import (
    detect_provider,
    load_api_keys_from_env,
    masked_input,
)


class FakeResponse:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _config(provider="deepseek", role="all", model="test-model"):
    base_url = (
        "https://api.anthropic.com/v1"
        if provider == "anthropic"
        else "https://example.test/v1"
    )
    return {
        "models": [{
            "provider": provider,
            "model": model,
            "api_key": "secret-key",
            "base_url": base_url,
            "role": role,
        }]
    }


def test_openai_compatible_request(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse(200, {
            "choices": [{"message": {"content": '{"ok": true}'}}]
        })

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    result = call_llm(_config(), "inspect this", timeout_seconds=7)

    assert result == '{"ok": true}'
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["json"]["messages"][1]["content"] == "inspect this"
    assert 0 < captured["timeout"] <= 7


def test_anthropic_uses_native_messages_protocol(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse(200, {
            "content": [
                {"type": "text", "text": "{\""},
                {"type": "text", "text": "ok\": true}"},
            ]
        })

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    result = call_llm(_config(provider="anthropic"), "inspect this")

    assert result == '{"ok": true}'
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "secret-key"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["system"]
    assert captured["json"]["messages"] == [
        {"role": "user", "content": "inspect this"}
    ]


def test_retries_rate_limit_then_succeeds(monkeypatch):
    responses = [
        FakeResponse(429, text="rate limited", headers={"retry-after": "0"}),
        FakeResponse(200, {"choices": [{"message": {"content": "done"}}]}),
    ]
    sleeps = []
    monkeypatch.setattr(
        "smartbench.llm.client.httpx.post", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr("smartbench.llm.client.time.sleep", sleeps.append)

    result = call_llm(_config(), "hello", max_retries=1)

    assert result == "done"
    assert sleeps == [0.0]
    assert responses == []


def test_does_not_retry_authentication_failure(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(401, text="invalid key")

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    result = call_llm(_config(), "hello", max_retries=3)

    assert result == ""
    assert len(calls) == 1


def test_routes_matching_role_first(monkeypatch):
    config = {
        "models": [
            {**_config(model="judge-model")["models"][0], "role": "judge"},
            {**_config(model="proposer-model")["models"][0], "role": "proposer"},
        ]
    }
    seen_models = []

    def fake_post(url, **kwargs):
        seen_models.append(kwargs["json"]["model"])
        return FakeResponse(200, {
            "choices": [{"message": {"content": "ok"}}]
        })

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    assert call_llm(config, "hello", role="proposer") == "ok"
    assert seen_models == ["proposer-model"]


def test_invalid_response_falls_back_to_next_model(monkeypatch):
    first = _config(model="first")["models"][0]
    second = _config(model="second")["models"][0]
    responses = [
        FakeResponse(200, {"choices": []}),
        FakeResponse(200, {"choices": [{"message": {"content": "fallback"}}]}),
    ]
    monkeypatch.setattr(
        "smartbench.llm.client.httpx.post", lambda *args, **kwargs: responses.pop(0)
    )

    assert call_llm({"models": [first, second]}, "hello") == "fallback"


def test_malformed_model_entries_are_skipped_before_valid_fallback(monkeypatch):
    errors = []
    valid = _config(model="valid-fallback")["models"][0]
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["model"])
        return FakeResponse(200, {
            "choices": [{"message": {"content": "ok"}}]
        })

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)
    result = call_llm(
        {
            "models": [
                None,
                "not-an-object",
                {
                    "provider": "deepseek",
                    "model": "broken",
                    "api_key": ["secret-must-not-leak"],
                    "base_url": "https://example.test/v1",
                },
                valid,
            ],
        },
        "hello",
        on_error=errors.append,
    )

    assert result == "ok"
    assert calls == ["valid-fallback"]
    assert len(errors) == 3
    assert all("secret-must-not-leak" not in error for error in errors)


def test_invalid_top_level_llm_configuration_returns_stable_error(monkeypatch):
    errors = []

    def fail_if_called(*args, **kwargs):
        raise AssertionError("invalid configuration must not reach the network")

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fail_if_called)

    assert call_llm(None, "hello", on_error=errors.append) == ""
    assert call_llm(
        {"models": "not-a-list"}, "hello", on_error=errors.append
    ) == ""
    assert "API configuration must be an object" in errors
    assert "API configuration 'models' must be a list" in errors


def test_invalid_retry_controls_fall_back_to_safe_defaults(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return FakeResponse(200, {
            "choices": [{"message": {"content": "ok"}}]
        })

    monkeypatch.setattr("smartbench.llm.client.httpx.post", fake_post)

    result = call_llm(
        _config(),
        "hello",
        timeout_seconds=float("nan"),
        max_retries="invalid",
    )

    assert result == "ok"
    assert 0 < captured["timeout"] <= 120


def test_provider_detection_and_environment_loading(monkeypatch):
    assert detect_provider("claude-sonnet-4")[:1] == ("anthropic",)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("SMARTBENCH_ANTHROPIC_MODEL", "claude-custom")
    for variable in (
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY",
        "DOUBAO_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    config = load_api_keys_from_env()

    assert config["models"][0]["provider"] == "anthropic"
    assert config["models"][0]["model"] == "claude-custom"


def test_environment_loading_honors_provider_base_url(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example/v1")
    for variable in (
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY",
        "DOUBAO_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    config = load_api_keys_from_env()

    assert config["models"][0]["base_url"] == "https://gateway.example/v1"


def test_smartbench_base_url_override_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv(
        "SMARTBENCH_ANTHROPIC_BASE_URL", "https://smartbench.example/v1"
    )
    for variable in (
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY",
        "DOUBAO_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    config = load_api_keys_from_env()

    assert config["models"][0]["base_url"] == "https://smartbench.example/v1"


def test_environment_model_does_not_force_manual_reconfiguration(monkeypatch):
    for variable in provider_module.ENV_PROVIDER_MAP:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    answers = iter((True, False))
    monkeypatch.setattr(
        provider_module.Confirm,
        "ask",
        lambda *_args, **_kwargs: next(answers),
    )

    def unexpected_prompt(*_args, **_kwargs):
        raise AssertionError("manual model input must remain optional")

    monkeypatch.setattr(provider_module.Prompt, "ask", unexpected_prompt)
    console = Console(file=StringIO(), color_system=None)

    config = provider_module.configure_api_keys(console)

    assert len(config["models"]) == 1
    assert config["models"][0]["provider"] == "openai"


def test_parse_json_safe_rejects_invalid_and_accepts_fences():
    assert parse_json_safe("not json") is None
    assert parse_json_safe("[]") is None
    assert parse_json_safe('```json\n{"ok": true}\n```') == {"ok": True}


def test_parse_json_safe_extracts_first_complete_object_from_prose():
    raw = 'Result: {"ok": true}\nExample: {"ok": false}'

    assert parse_json_safe(raw) == {"ok": True}


def test_errors_do_not_include_api_key(monkeypatch):
    errors = []
    monkeypatch.setattr(
        "smartbench.llm.client.httpx.post",
        lambda *args, **kwargs: FakeResponse(
            401, text="secret-key was rejected"
        ),
    )

    call_llm(_config(), "hello", on_error=errors.append)

    assert errors
    assert all("secret-key" not in message for message in errors)
    assert "[REDACTED] was rejected" in errors[0]
    assert json.dumps(errors)


def test_masked_input_does_not_echo_any_key_fragment(monkeypatch):
    stream = StringIO()
    console = Console(file=stream, color_system=None)
    secret = "sk-super-secret-last4"
    monkeypatch.setattr(provider_module._sys, "platform", "linux")
    monkeypatch.setattr(provider_module, "_masked_input_unix", lambda: secret)

    assert masked_input(
        console,
        "API key [link=https://evil.example]click[/link] "
        "\x1b]8;;https://evil.example\x1b\\hidden\x1b]8;;\x1b\\",
    ) == secret

    output = stream.getvalue()
    assert "sk-" not in output
    assert "last4" not in output
    assert "saved: ****" in output
    assert "[link=https://evil.example]click[/link]" in output
    assert "hidden" in output
    assert "\x1b" not in output
