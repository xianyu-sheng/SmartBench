"""Protocol and reliability tests for the LLM integration layer."""

import json

from smartbench.llm.client import call_llm, parse_json_safe
from smartbench.llm.provider import detect_provider, load_api_keys_from_env


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


def test_parse_json_safe_rejects_invalid_and_accepts_fences():
    assert parse_json_safe("not json") is None
    assert parse_json_safe("[]") is None
    assert parse_json_safe('```json\n{"ok": true}\n```') == {"ok": True}


def test_errors_do_not_include_api_key(monkeypatch):
    errors = []
    monkeypatch.setattr(
        "smartbench.llm.client.httpx.post",
        lambda *args, **kwargs: FakeResponse(401, text="invalid credentials"),
    )

    call_llm(_config(), "hello", on_error=errors.append)

    assert errors
    assert all("secret-key" not in message for message in errors)
    assert json.dumps(errors)
