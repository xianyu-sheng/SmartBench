"""Language frontend boundary.

Concrete implementations remain in ``smartbench.core.adapters`` for
backwards compatibility.  New integrations can use the architectural names
here: a frontend lowers source projects into SemanticIR.
"""

from smartbench.core.adapters.base import (
    AdapterRegistry,
    FrontendRegistry,
    LanguageAdapter,
    LanguageFrontend,
)

__all__ = [
    "AdapterRegistry",
    "FrontendRegistry",
    "LanguageAdapter",
    "LanguageFrontend",
]
