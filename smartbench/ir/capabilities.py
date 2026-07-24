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


class CapabilityLevel(str, Enum):
    """Strength at which a semantic capability is available.

    ``PARTIAL`` is intentionally a first-class result.  A backend may use a
    conservative, intra-procedural approximation, but it must not advertise
    that approximation as a complete implementation.  Keeping the ordering
    here lets rule contracts ask for a minimum strength without adding
    language-specific exceptions in the engine.
    """

    UNSUPPORTED = "unsupported"
    PARTIAL = "partial"
    FULL = "full"

    @property
    def rank(self) -> int:
        return {
            CapabilityLevel.UNSUPPORTED: 0,
            CapabilityLevel.PARTIAL: 1,
            CapabilityLevel.FULL: 2,
        }[self]


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

    def level(self, capability: Capability | str) -> CapabilityLevel:
        """Return the effective strength of one capability."""
        normalized = capability if isinstance(capability, Capability) else Capability(capability)
        if normalized in self.supported:
            return CapabilityLevel.FULL
        if normalized in self.partial:
            return CapabilityLevel.PARTIAL
        return CapabilityLevel.UNSUPPORTED

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

    def assess(
        self,
        required: Mapping[Capability | str, CapabilityLevel | str],
    ) -> dict[str, object]:
        """Assess a rule contract against this frontend's capabilities."""
        capabilities: dict[str, dict[str, str]] = {}
        overall = CapabilityLevel.FULL
        for requested, minimum in required.items():
            capability = requested if isinstance(requested, Capability) else Capability(requested)
            minimum_level = (
                minimum
                if isinstance(minimum, CapabilityLevel)
                else CapabilityLevel(minimum)
            )
            actual = self.level(capability)
            status = (
                CapabilityLevel.FULL
                if actual.rank >= minimum_level.rank and actual == CapabilityLevel.FULL
                else CapabilityLevel.PARTIAL
                if actual.rank >= minimum_level.rank
                else CapabilityLevel.UNSUPPORTED
            )
            if status.rank < overall.rank:
                overall = status
            capabilities[capability.value] = {
                "required": minimum_level.value,
                "actual": actual.value,
                "status": status.value,
                "reason": self.partial.get(capability, "") if actual == CapabilityLevel.PARTIAL else "",
            }
        return {"status": overall.value, "capabilities": capabilities}

    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "supported": sorted(value.value for value in self.supported),
            "partial": {
                key.value: value
                for key, value in sorted(self.partial.items(), key=lambda item: item[0].value)
            },
        }
