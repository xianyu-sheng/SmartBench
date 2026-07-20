"""
LLM Chat Client — OpenAI-compatible API wrapper with role-aware routing.

Supports:
  - Multi-model role routing (Proposer / Critique / Judge)
  - Auto model name resolution from provider
  - Old config format fallback
  - Retry with exponential backoff
"""

import json
import re
import time
import logging
from typing import Dict, List, Optional, Callable

from smartbench.llm.provider import PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

# Default model per provider when model="auto"
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


def call_llm(
    api_config: Dict,
    prompt: str,
    system: str = (
        "你是一位资深软件工程师。请用中文回复。只返回要求的 JSON，不要其他内容。"
    ),
    role: str = "",
    max_retries: int = 2,
    on_error: Optional[Callable[[str], None]] = None,
) -> str:
    """Call an LLM via OpenAI-compatible API with role-aware routing.

    Args:
        api_config: {"models": [{"provider":..., "model":..., "api_key":...,
                     "base_url":..., "role":...}, ...]}
        prompt: The user prompt.
        system: System prompt.
        role: "proposer" / "critique" / "judge" — routes to the correct model.
        max_retries: Max retries per model on transient failures.
        on_error: Optional callback(provider_name, error_message) for logging.

    Returns:
        LLM response text, or "" if all models failed.
    """
    import urllib.request
    import urllib.error

    models = api_config.get("models", [])

    # Fallback: old format {"deepseek": "sk-...", "openai": "sk-..."}
    if not models and isinstance(api_config, dict):
        models = _convert_old_format(api_config)

    if not models:
        logger.warning("call_llm: no models configured")
        return ""

    # Route by role: prefer model assigned to this role, fallback to "all", then rest
    ordered = sorted(
        models,
        key=lambda m: (
            0 if m.get("role") == role else (1 if m.get("role") == "all" else 2)
        ),
    )

    for m in ordered:
        api_key = m.get("api_key", "")
        if not api_key:
            continue

        base_url = m.get("base_url", "").rstrip("/")
        model_name = m.get("model", "auto")
        provider = m.get("provider", "unknown")

        if model_name == "auto":
            model_name = _DEFAULT_MODELS.get(provider, "gpt-3.5-turbo")

        url = f"{base_url}/chat/completions"
        body = json.dumps({
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {api_key}")

        for attempt in range(1 + max_retries):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8", errors="ignore")[:300]
                msg = f"{provider} HTTP {e.code}: {error_body}"
                logger.warning(msg)
                if on_error:
                    on_error(msg)
                # Don't retry on 4xx (client errors like bad key)
                if 400 <= e.code < 500:
                    break
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                continue
            except Exception as e:
                msg = f"{provider} error: {e}"
                logger.warning(msg)
                if on_error:
                    on_error(msg)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                continue

    return ""


def _convert_old_format(api_config: Dict) -> List[Dict]:
    """Convert old flat format to new models list format.

    Old: {"deepseek": "sk-...", "openai": "sk-..."}
    New: {"models": [{"provider": "deepseek", "model": "auto", ...}]}
    """
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
    """Safely parse JSON from LLM output, handling markdown fences.

    >>> parse_json_safe('{"a": 1}')
    {'a': 1}
    >>> parse_json_safe('```json\\n{"a": 1}\\n```')
    {'a': 1}
    >>> parse_json_safe('Some text\\n{"a": 1}\\nMore text')
    {'a': 1}
    >>> parse_json_safe(None)
    >>> parse_json_safe('not json')
    """
    if not raw:
        return None

    cleaned = raw.strip()

    # Remove markdown code fences
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object from text
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
