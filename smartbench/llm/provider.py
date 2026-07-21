"""
LLM Provider Registry — model detection, API key management, and authentication.

Auto-detects provider (base URL + display name) from model name prefix.
All API keys are stored in memory only — never persisted to disk.
"""

import os
import sys as _sys
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.prompt import Confirm, Prompt

# ═══════════════════════════════════════════════════════════════════════
# Provider Registry
# ═══════════════════════════════════════════════════════════════════════

PROVIDER_REGISTRY: Dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "patterns": ["deepseek"],
        "display": "DeepSeek",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "patterns": ["gpt-", "o1-", "o3-", "o4-"],
        "display": "OpenAI",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "patterns": ["claude-"],
        "display": "Anthropic",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "patterns": ["glm-", "chatglm", "cogview"],
        "display": "Zhipu GLM",
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "patterns": ["doubao-", "seed-"],
        "display": "ByteDance Doubao",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "patterns": ["moonshot-", "kimi"],
        "display": "Moonshot Kimi",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "patterns": ["qwen-", "qwq-"],
        "display": "Alibaba Qwen",
    },
    "local": {
        "base_url": "http://localhost:11434/v1",
        "patterns": ["llama", "mistral", "qwen2", "codellama", "deepseek-r1"],
        "display": "Local (Ollama-compatible)",
    },
}

# Environment variable → (provider_key, default_model) mapping
ENV_PROVIDER_MAP: Dict[str, Tuple[str, str]] = {
    "DEEPSEEK_API_KEY": ("deepseek", "deepseek-chat"),
    "OPENAI_API_KEY": ("openai", "gpt-4o"),
    "ANTHROPIC_API_KEY": ("anthropic", "claude-sonnet-4-20250514"),
    "GLM_API_KEY": ("glm", "glm-4-0520"),
    "DOUBAO_API_KEY": ("doubao", "doubao-seed-2.0-pro-260215"),
    "MOONSHOT_API_KEY": ("moonshot", "moonshot-v1-8k"),
    "DASHSCOPE_API_KEY": ("qwen", "qwen-max"),
}

ROLE_KEYS = ["proposer", "critique", "judge"]
ROLE_NAMES_CN = ["Proposer（方案提出者）", "Critique（交叉审查者）", "Judge（最终仲裁者）"]
ROLE_COLORS = ["cyan", "yellow", "green"]


# ═══════════════════════════════════════════════════════════════════════
# Provider Detection
# ═══════════════════════════════════════════════════════════════════════

def detect_provider(model_name: str) -> Tuple[str, str, str]:
    """Given a model name, return (provider_key, base_url, display_name).

    >>> detect_provider("deepseek-chat")
    ('deepseek', 'https://api.deepseek.com/v1', 'DeepSeek')
    >>> detect_provider("gpt-4o")
    ('openai', 'https://api.openai.com/v1', 'OpenAI')
    >>> detect_provider("unknown-model")
    ('openai', 'https://api.openai.com/v1', 'OpenAI-compatible')
    """
    model_lower = model_name.lower().strip()
    for key, info in PROVIDER_REGISTRY.items():
        for pattern in info["patterns"]:
            if model_lower.startswith(pattern):
                return (key, info["base_url"], info["display"])
    # Fallback: treat as OpenAI-compatible generic
    return ("openai", "https://api.openai.com/v1", "OpenAI-compatible")


# ═══════════════════════════════════════════════════════════════════════
# API Key Loading (zero-UI — safe for non-interactive use)
# ═══════════════════════════════════════════════════════════════════════

def load_api_keys_from_env() -> Optional[Dict]:
    """Load API keys from environment variables (quick mode / CI).

    Returns:
        {"models": [{"provider": ..., "model": ..., "api_key": ..., "base_url": ..., "role": ...}]}
        or None if no keys found.
    """
    models = []
    for env_var, (provider, default_model) in ENV_PROVIDER_MAP.items():
        key = os.environ.get(env_var, "")
        if key:
            info = PROVIDER_REGISTRY.get(provider, {})
            models.append({
                "provider": provider,
                "model": default_model,
                "api_key": key,
                "base_url": info.get("base_url", ""),
            })

    _assign_roles(models)
    return {"models": models} if models else None


def _assign_roles(models: List[Dict]) -> None:
    """Assign debate roles to models in-place.

    If 3+ models: one per role (proposer, critique, judge).
    Otherwise: all models get role="all".
    """
    if len(models) >= 3:
        for i, role in enumerate(ROLE_KEYS):
            models[i]["role"] = role
    else:
        for m in models:
            m["role"] = "all"


# ═══════════════════════════════════════════════════════════════════════
# Interactive Configuration
# ═══════════════════════════════════════════════════════════════════════

def configure_api_keys(console: Console) -> Optional[Dict]:
    """Interactive model + API key configuration — role-aware.

    User chooses:
      [1] One model for all three roles (convenient)
      [2] Different models for Proposer / Critique / Judge (credible debate)

    Keys stored in memory only — never persisted to disk.

    Args:
        console: Rich Console instance for output.

    Returns:
        {"models": [...]} or None if user cancelled.
    """
    models_list = []

    console.print("\n  [dim]Keys stored in memory only — restart terminal to reconfigure.[/dim]")

    # Step A: Environment variable quick-load
    for provider in ENV_PROVIDER_MAP:
        key = os.environ.get(provider, "")
        if key:
            info = PROVIDER_REGISTRY.get(
                ENV_PROVIDER_MAP[provider][0], {}
            )
            display = info.get("display", provider)
            if Confirm.ask(
                f"  Use ${provider} from env? ({display})", default=True
            ):
                models_list.append({
                    "provider": ENV_PROVIDER_MAP[provider][0],
                    "model": "auto",
                    "api_key": key,
                    "base_url": info.get("base_url", ""),
                    "role": "all",
                })
                console.print(f"    [green]OK[/green] {display}")

    if len(models_list) >= 3:
        for i, m in enumerate(models_list[:3]):
            m["role"] = ROLE_KEYS[i]
            console.print(f"    [dim]{ROLE_NAMES_CN[i]} → {m.get('model', 'auto')}[/dim]")

    # Step B: Choose config mode
    console.print("\n  [bold]How to configure?[/bold]")
    console.print("  [1] One model — all three roles share it (convenient)")
    console.print("  [2] Three models — Proposer / Critique / Judge each use a different model (credible debate)")

    choice = Prompt.ask("  Choice", default="1", choices=["1", "2"]).strip()

    # Step C: Collect model(s)
    if choice == "1":
        console.print("\n  [bold]Configure the model for all three roles:[/bold]")
        console.print("  [dim]Examples: deepseek-chat | gpt-4o | claude-sonnet-4[/dim]")
        model = _prompt_single_model(console)
        if model:
            model["role"] = "all"
            models_list.append(model)
            console.print(f"    [green]OK[/green] Proposer / Critique / Judge 共用 [{model['model']}]")
    else:
        console.print("\n  [bold]Configure one model per role:[/bold]")
        console.print("  [dim]For maximum credibility, use different models for each role.[/dim]")
        for role_key, role_name, role_color in zip(ROLE_KEYS, ROLE_NAMES_CN, ROLE_COLORS):
            console.print(f"\n  [{role_color}]── {role_name} ──[/{role_color}]")
            model = _prompt_single_model(console)
            if not model:
                console.print("    [yellow]Skipped — this role will use the first available model[/yellow]")
                continue
            model["role"] = role_key
            models_list.append(model)

    if not models_list:
        return None

    console.print(f"\n  [green]Ready![/green] {len(models_list)} model(s) configured.")
    if any(m.get("role") == "all" for m in models_list):
        console.print("  [dim]One model → all three debate roles share it.[/dim]")
    else:
        for m in models_list:
            role = m.get("role", "?")
            role_display = dict(zip(ROLE_KEYS, ROLE_NAMES_CN)).get(role, role)
            console.print(f"  [dim]{role_display} → {m['model']} ({m.get('provider', '?')})[/dim]")

    return {"models": models_list}


def _prompt_single_model(console: Console) -> Optional[Dict]:
    """Prompt for a single model name + API key. Returns model dict or None."""
    model = Prompt.ask("    Model name", default="").strip()
    if not model:
        return None

    provider_key, base_url, display = detect_provider(model)
    console.print(f"      [dim]Provider: {display} → {base_url}[/dim]")
    override = Prompt.ask("      Base URL (Enter to confirm)", default="").strip()
    if override:
        base_url = override

    key = masked_input(console, f"      API key for {model}")
    if not key:
        return None

    return {
        "provider": provider_key,
        "model": model,
        "api_key": key,
        "base_url": base_url,
    }


# ═══════════════════════════════════════════════════════════════════════
# Secure Input (cross-platform)
# ═══════════════════════════════════════════════════════════════════════

def masked_input(console: Console, prompt_text: str) -> str:
    """Read a secret with * echo for each character typed.

    Args:
        console: Rich Console instance (for styled output).
        prompt_text: The prompt to show before reading input.

    Returns:
        The entered string (never displayed in plaintext).
    """
    console.print(f"  {prompt_text}: ", end="")

    if _sys.platform == "win32":
        value = _masked_input_windows()
    else:
        value = _masked_input_unix()

    # Show masked confirmation
    if value:
        mask = value[:3] + "****" + value[-4:] if len(value) > 10 else "****"
        console.print(f"    [dim]saved: {mask}[/dim]")
    return value


def _masked_input_windows() -> str:
    """Windows implementation using msvcrt."""
    import msvcrt

    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print()
            break
        if ch == "\x08":  # backspace
            if chars:
                chars.pop()
                print("\b \b", end="")
            continue
        if ch == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        chars.append(ch)
        print("*", end="")
    return "".join(chars)


def _masked_input_unix() -> str:
    """Unix implementation using termios."""
    import termios
    import tty

    fd = _sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        chars = []
        while True:
            ch = _sys.stdin.read(1)
            if ch in ("\r", "\n"):
                print()
                break
            if ch == "\x7f":  # backspace
                if chars:
                    chars.pop()
                    print("\b \b", end="")
                continue
            if ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            chars.append(ch)
            print("*", end="")
        return "".join(chars)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
