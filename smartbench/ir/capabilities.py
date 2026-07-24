"""Capabilities exposed by language frontends.

Capabilities are deliberately explicit.  A language being recognized is not
the same as a language being semantically supported by every analyzer.  The
engine can therefore report ``unknown``/``unsupported`` instead of silently
classifying a file as clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping


class Capability(str, Enum):
    """Features that a frontend may expose to analysis backends."""

    STRUCTURE = "structure"
    SOURCE_LOCATIONS = "source_locations"
    SYMBOLS = "symbols"
    CALL_GRAPH = "call_graph"
    CONTROL_FLOW = "control_flow"
    DATA_FLOW = "data_flow"
    TYPE_INFO = "type_info"
    CONCURRENCY = "concurrency"
    EVENT_MODEL = "event_model"
    TEST_MAPPING = "test_mapping"


@dataclass(frozen=True)
class CapabilitySet:
    """Capabilities for one language frontend.

    ``partial`` records capabilities that are available with an explicit
    limitation.  This keeps the common interface honest while still allowing
    an analyzer to make a controlled best-effort decision.
    """

    language: str
    supported: frozenset[Capability] = field(default_factory=frozenset)
    partial: Mapping[Capability, str] = field(default_factory=dict)

    @classmethod
    def from_values(
        cls,
        language: str,
        supported: Iterable[Capability | str] = (),
        partial: Mapping[Capability | str, str] | None = None,
    ) -> "CapabilitySet":
        normalized = frozenset(
            value if isinstance(value, Capability) else Capability(value)
            for value in supported
        )
        partial_values = {
            key if isinstance(key, Capability) else Capability(key): value
            for key, value in (partial or {}).items()
        }
        return cls(language=language, supported=normalized, partial=partial_values)

    def supports(self, capability: Capability | str) -> bool:
        """Return whether a capability is available at full strength."""
        normalized = capability if isinstance(capability, Capability) else Capability(capability)
        return normalized in self.supported

    def is_partial(self, capability: Capability | str) -> bool:
        normalized = capability if isinstance(capability, Capability) else Capability(capability)
        return normalized in self.partial

    def missing(self, required: Iterable[Capability | str]) -> list[str]:
        """Return required capabilities that are not fully supported."""
        return [
            (value.value if isinstance(value, Capability) else value)
            for value in required
            if not self.supports(value)
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "supported": sorted(value.value for value in self.supported),
            "partial": {
                key.value: value
                for key, value in sorted(self.partial.items(), key=lambda item: item[0].value)
            },
        }
