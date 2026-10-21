"""Role-aware LLM client for OpenAI-compatible and Anthropic APIs."""

import json
import logging
import math
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from smartbench.llm.provider import PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

_DEFAULT_MODELS: Dict[str, str] = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
    "glm": "glm-4-0520",
    "doubao": "doubao-seed-2.0-pro-260215",
    "anthropic": "claude-sonnet-4-20250514",
    "moonshot": "moonshot-v1-8k",
    "qwen": "qwen-max",
    "local": "llama3",
}

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

_DEFAULT_MAX_OUTPUT_TOKENS = 8192
_MAX_OUTPUT_TOKENS_CAP = 65536


class EmptyCompletionError(ValueError):
    """The API returned a successful response with no text content.

    Reasoning models can consume the whole ``max_tokens`` budget in hidden
    reasoning tokens and return an empty ``content`` field (typically with
    ``finish_reason=length``). This is retryable with a larger budget.
    """


def _max_output_tokens() -> int:
    """Resolve the output-token budget, honoring an explicit override.

    ``SMARTBENCH_LLM_MAX_TOKENS`` sets the initial budget. Reasoning-capable
    providers may need headroom above the visible answer; the default leaves
    room for both the reasoning trace and the requested JSON.
    """
    raw = os.environ.get("SMARTBENCH_LLM_MAX_TOKENS", "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_OUTPUT_TOKENS
    return max(256, min(value, _MAX_OUTPUT_TOKENS_CAP))


def _deepseek_thinking_disabled() -> bool:
    """Whether DeepSeek hidden reasoning should be explicitly disabled.

    ``SMARTBENCH_DEEPSEEK_DISABLE_THINKING=1`` sends
    ``thinking: {"type": "disabled"}`` for structured-output calls. The
    default keeps provider behavior untouched.
    """
    raw = os.environ.get("SMARTBENCH_DEEPSEEK_DISABLE_THINKING", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def call_llm(
    api_config: Dict,
    prompt: str,
    system: str = (
        "你是一位资深软件工程师。请用中文回复。只返回要求的 JSON，不要其他内容。"
    ),
    role: str = "",
    max_retries: int = 2,
    timeout_seconds: float = 120,
    on_error: Optional[Callable[[str], None]] = None,
    temperature: float = 0.3,
    seed: Optional[int] = None,
    on_usage: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> str:
    """Call the best configured model for *role* and return response text.

    Anthropic uses its native Messages request and response schema. Every
    other registered provider uses the OpenAI-compatible chat-completions
    schema. Failed providers are tried in role-preference order.
    """
    models = _normalize_model_configs(api_config, on_error)
    if not models:
        _report_error("No models configured", on_error)
        return ""
    secret_values = [model["api_key"] for model in models]

    def report_error(message: str) -> None:
        _report_error(message, on_error, secrets=secret_values)

    try:
        normalized_timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        normalized_timeout = 120.0
    if not math.isfinite(normalized_timeout):
        normalized_timeout = 120.0
    deadline = time.monotonic() + max(normalized_timeout, 0.001)
    try:
        max_retries = max(0, int(max_retries))
    except (TypeError, ValueError):
        max_retries = 2
    try:
        normalized_temperature = float(temperature)
    except (TypeError, ValueError):
        normalized_temperature = 0.3
    if not math.isfinite(normalized_temperature) or not 0.0 <= normalized_temperature <= 2.0:
        normalized_temperature = 0.3
    normalized_seed = seed if isinstance(seed, int) and not isinstance(seed, bool) else None

    ordered = sorted(
        models,
        key=lambda model: (
            0 if model.get("role") == role
            else (1 if model.get("role") == "all" else 2)
        ),
    )

    for config in ordered:
        api_key = config.get("api_key", "")
        if not api_key:
            continue
        provider = config.get("provider", "unknown")
        base_url = config.get("base_url", "").rstrip("/")
        if not base_url:
            report_error(f"{provider}: missing base URL")
            continue
        model_name = config.get("model", "auto")
        if model_name == "auto":
            model_name = _DEFAULT_MODELS.get(provider, "gpt-4o")

        url, headers, body = _build_request(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            prompt=prompt,
            system=system,
            temperature=normalized_temperature,
            seed=normalized_seed,
        )

        for attempt in range(max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report_error("LLM call timeout budget exhausted")
                return ""
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=remaining,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                report_error(f"{provider} transport error: {exc}")
                if attempt < max_retries:
                    if not _sleep_before_deadline(2 ** attempt, deadline):
                        return ""
                    continue
                break
            except Exception as exc:
                report_error(f"{provider} client error: {exc}")
                break

            if 200 <= response.status_code < 300:
                try:
                    payload = response.json()
                    text = _extract_content(provider, payload)
                    if on_usage is not None:
                        on_usage(_extract_usage(provider, payload, model_name))
                    return text
                except EmptyCompletionError as exc:
                    # Reasoning models may exhaust the token budget on hidden
                    # reasoning and return empty content. Retry with a larger
                    # budget before falling back to another provider.
                    report_error(f"{provider} empty completion: {exc}")
                    if attempt < max_retries:
                        body["max_tokens"] = min(
                            int(body.get("max_tokens", _DEFAULT_MAX_OUTPUT_TOKENS)) * 2,
                            _MAX_OUTPUT_TOKENS_CAP,
                        )
                        if not _sleep_before_deadline(2 ** attempt, deadline):
                            return ""
                        continue
                    break
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    report_error(f"{provider} invalid response: {exc}")
                    break

            message = f"{provider} HTTP {response.status_code}: {response.text[:300]}"
            report_error(message)
            if response.status_code not in _RETRYABLE_STATUS or attempt >= max_retries:
                break
            if not _sleep_before_deadline(_retry_delay(response, attempt), deadline):
                return ""

    return ""


def _normalize_model_configs(
    api_config: Any,
    on_error: Optional[Callable[[str], None]],
) -> List[Dict[str, str]]:
    """Validate external model configuration without exposing field values."""
    if not isinstance(api_config, dict):
        _report_error("API configuration must be an object", on_error)
        return []

    raw_models = api_config.get("models", [])
    if not raw_models:
        raw_models = _convert_old_format(api_config)
    if not isinstance(raw_models, list):
        _report_error("API configuration 'models' must be a list", on_error)
        return []

    normalized: List[Dict[str, str]] = []
    for index, raw_model in enumerate(raw_models[:32], start=1):
        prefix = f"Model configuration #{index}"
        if not isinstance(raw_model, dict):
            _report_error(f"{prefix} must be an object; skipped", on_error)
            continue

        api_key = raw_model.get("api_key", "")
        provider = raw_model.get("provider", "unknown")
        base_url = raw_model.get("base_url", "")
        model_name = raw_model.get("model", "auto")
        role = raw_model.get("role", "all")

        invalid_fields = []
        if not isinstance(api_key, str) or not api_key.strip():
            invalid_fields.append("api_key")
        if not isinstance(provider, str) or not provider.strip():
            invalid_fields.append("provider")
        if not isinstance(base_url, str) or not base_url.strip():
            invalid_fields.append("base_url")
        if not isinstance(model_name, str) or not model_name.strip():
            invalid_fields.append("model")
        if not isinstance(role, str) or not role.strip():
            invalid_fields.append("role")
        if invalid_fields:
            _report_error(
                f"{prefix} has invalid fields: {', '.join(invalid_fields)}; skipped",
                on_error,
            )
            continue

        normalized.append({
            "api_key": api_key.strip(),
            "provider": provider.strip(),
            "base_url": base_url.strip(),
            "model": model_name.strip(),
            "role": role.strip(),
        })

    if len(raw_models) > 32:
        _report_error(
            "Only the first 32 model configurations are considered",
            on_error,
        )
    return normalized


def _build_request(
    provider: str,
    base_url: str,
    api_key: str,
    model_name: str,
    prompt: str,
    system: str,
    temperature: float = 0.3,
    seed: Optional[int] = None,
) -> Tuple[str, Dict[str, str], Dict]:
    """Build a provider-specific HTTP request without exposing credentials."""
    if provider == "anthropic":
        return (
            f"{base_url}/messages",
            {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            {
                "model": model_name,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": _max_output_tokens(),
            },
        )

    body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": _max_output_tokens(),
    }
    if seed is not None:
        body["seed"] = seed
    if provider == "deepseek" and _deepseek_thinking_disabled():
        body["thinking"] = {"type": "disabled"}

    return (
        f"{base_url}/chat/completions",
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        body,
    )


def _extract_content(provider: str, payload: Dict) -> str:
    if provider == "anthropic":
        blocks = payload["content"]
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        text = payload["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise EmptyCompletionError("response contained no text")
    return text


def _extract_usage(provider: str, payload: Dict, model_name: str) -> Dict[str, Any]:
    """Normalize provider usage fields into one comparable record."""
    raw = payload.get("usage") if isinstance(payload, dict) else None
    usage = raw if isinstance(raw, dict) else {}
    if provider == "anthropic":
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
    else:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
    try:
        prompt_count = max(0, int(prompt_tokens or 0))
        completion_count = max(0, int(completion_tokens or 0))
    except (TypeError, ValueError):
        prompt_count = 0
        completion_count = 0
    return {
        "model": model_name,
        "prompt_tokens": prompt_count,
        "completion_tokens": completion_count,
        "total_tokens": prompt_count + completion_count,
    }


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("retry-after", "")
    try:
        return min(max(float(value), 0.0), 30.0) if value else 2 ** attempt
    except ValueError:
        return 2 ** attempt


def _sleep_before_deadline(delay: float, deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0 or delay >= remaining:
        return False
    time.sleep(delay)
    return True


def _report_error(
    message: str,
    callback: Optional[Callable[[str], None]],
    secrets: Optional[List[str]] = None,
) -> None:
    safe_message = str(message)
    for secret in secrets or []:
        if secret:
            safe_message = safe_message.replace(secret, "[REDACTED]")
    logger.warning(safe_message)
    if callback:
        try:
            callback(safe_message)
        except Exception:
            logger.debug("LLM error callback failed", exc_info=True)


def _convert_old_format(api_config: Dict) -> List[Dict]:
    """Convert ``{"deepseek": "sk-..."}`` to the model-list format."""
    models = []
    for provider in PROVIDER_REGISTRY:
        key = api_config.get(provider, "")
        if key:
            info = PROVIDER_REGISTRY.get(provider, {})
            models.append({
                "provider": provider,
                "model": "auto",
                "api_key": key,
                "base_url": info.get("base_url", ""),
                "role": "all",
            })
    return models


def parse_json_safe(raw: str) -> Optional[Dict]:
    """Parse JSON from a raw model response, including fenced output."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    # Decode the first complete object instead of greedily taking everything
    # between the first and last brace. Models often add prose or a second
    # example object after the requested answer.
    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned, index)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
