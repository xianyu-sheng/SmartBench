"""
LLM integration layer — provider detection, API key management, and chat client.
"""

from smartbench.llm.provider import (
    PROVIDER_REGISTRY,
    ENV_PROVIDER_MAP,
    ROLE_KEYS,
    ROLE_NAMES_CN,
    detect_provider,
    load_api_keys_from_env,
    configure_api_keys,
    masked_input,
)
from smartbench.llm.client import (
    call_llm,
    parse_json_safe,
)

__all__ = [
    "PROVIDER_REGISTRY",
    "ENV_PROVIDER_MAP",
    "ROLE_KEYS",
    "ROLE_NAMES_CN",
    "detect_provider",
    "load_api_keys_from_env",
    "configure_api_keys",
    "masked_input",
    "call_llm",
    "parse_json_safe",
]
