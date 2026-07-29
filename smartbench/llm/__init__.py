"""
LLM integration layer — provider detection, API key management, and chat client.
"""

from smartbench.llm.client import (
    call_llm,
    parse_json_safe,
)
from smartbench.llm.provider import (
    ENV_PROVIDER_MAP,
    PROVIDER_REGISTRY,
    ROLE_KEYS,
    ROLE_NAMES_CN,
    configure_api_keys,
    detect_provider,
    load_api_keys_from_env,
    masked_input,
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
