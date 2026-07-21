"""Role-aware LLM client for OpenAI-compatible and Anthropic APIs."""

import json
import logging
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

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
) -> str:
    """Call the best configured model for *role* and return response text.

    Anthropic uses its native Messages request and response schema. Every
    other registered provider uses the OpenAI-compatible chat-completions
    schema. Failed providers are tried in role-preference order.
    """
    models = api_config.get("models", [])
    if not models and isinstance(api_config, dict):
        models = _convert_old_format(api_config)
    if not models:
        _report_error("No models configured", on_error)
        return ""
    deadline = time.monotonic() + max(float(timeout_seconds), 0.001)
    max_retries = max(0, int(max_retries))

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
            _report_error(f"{provider}: missing base URL", on_error)
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
        )

        for attempt in range(max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _report_error("LLM call timeout budget exhausted", on_error)
                return ""
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=remaining,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                _report_error(f"{provider} transport error: {exc}", on_error)
                if attempt < max_retries:
                    if not _sleep_before_deadline(2 ** attempt, deadline):
                        return ""
                    continue
                break
            except Exception as exc:
                _report_error(f"{provider} client error: {exc}", on_error)
                break

            if 200 <= response.status_code < 300:
                try:
                    return _extract_content(provider, response.json())
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    _report_error(f"{provider} invalid response: {exc}", on_error)
                    break

            message = f"{provider} HTTP {response.status_code}: {response.text[:300]}"
            _report_error(message, on_error)
            if response.status_code not in _RETRYABLE_STATUS or attempt >= max_retries:
                break
            if not _sleep_before_deadline(_retry_delay(response, attempt), deadline):
                return ""

    return ""


def _build_request(
    provider: str,
    base_url: str,
    api_key: str,
    model_name: str,
    prompt: str,
    system: str,
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
                "temperature": 0.3,
                "max_tokens": 4096,
            },
        )

    return (
        f"{base_url}/chat/completions",
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        },
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
        raise ValueError("response contained no text")
    return text


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


def _report_error(message: str, callback: Optional[Callable[[str], None]]) -> None:
    logger.warning(message)
    if callback:
        try:
            callback(message)
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
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            parsed = json.loads(match.group())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None
