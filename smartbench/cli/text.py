"""Safe terminal rendering for repository and model supplied text."""

from typing import Any

from rich.markup import escape
from rich.text import Text


def safe_terminal_text(value: Any) -> str:
    """Strip ANSI/OSC controls and escape Rich markup."""
    plain = Text.from_ansi(str(value)).plain
    return escape(plain)
