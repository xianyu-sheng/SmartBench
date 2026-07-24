"""Canonical semantic IR that separates language frontends from analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from smartbench.graph.schema import CodeGraph
from smartbench.ir.capabilities import Capability, CapabilitySet
from smartbench.ir.evidence import SemanticFact
from smartbench.path_safety import read_text_bounded, resolve_project_file


@dataclass(frozen=True)
class SourceUnit:
    """Source-file metadata kept in the IR without eagerly duplicating content."""

    file_path: str
    language: str
    line_count: int = 0
    content_hash: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "line_count": self.line_count,
            "content_hash": self.content_hash,
        }


@dataclass
class SemanticIR:
    """Versioned, language-neutral analysis input.

    ``graph`` is the structural graph retained for compatibility with older
    SmartBench rules.  New analyzers should use this object and its capability
    and evidence contracts rather than importing a language parser directly.
    """

    graph: CodeGraph
    project_path: str = ""
    languages: tuple[str, ...] = field(default_factory=tuple)
    capabilities: dict[str, CapabilitySet] = field(default_factory=dict)
    source_units: dict[str, SourceUnit] = field(default_factory=dict)
    facts: list[SemanticFact] = field(default_factory=list)
    schema_version: str = "semantic-ir/v1"

    @classmethod
    def from_graph(
        cls,
        graph: CodeGraph,
        *,
        language: str | None = None,
        capabilities: CapabilitySet | None = None,
        project_path: str | None = None,
    ) -> "SemanticIR":
        root = project_path or str(graph.meta.get("project_path", ""))
        languages = {node.language for node in graph.nodes.values() if node.language}
        if language:
            languages.add(language)
        units: dict[str, SourceUnit] = {}
        for node in graph.nodes.values():
            if not node.file_path:
                continue
            units.setdefault(
                node.file_path,
                SourceUnit(file_path=node.file_path, language=node.language or language or "unknown"),
            )
        capability_map = dict(capabilities and {capabilities.language: capabilities} or {})
        for detected in languages:
            capability_map.setdefault(
                detected,
                CapabilitySet.from_values(
                    detected,
                    [Capability.STRUCTURE, Capability.SOURCE_LOCATIONS, Capability.SYMBOLS],
                    partial={Capability.CALL_GRAPH: "derived from structural graph; resolution may be heuristic"},
                ),
            )
        return cls(
            graph=graph,
            project_path=root,
            languages=tuple(sorted(languages)),
            capabilities=capability_map,
            source_units=units,
        )

    @property
    def nodes(self):
        """Compatibility view for existing graph consumers."""
        return self.graph.nodes

    @property
    def edges(self):
        """Compatibility view for existing graph consumers."""
        return self.graph.edges

    @property
    def meta(self) -> dict[str, Any]:
        return self.graph.meta

    def __getattr__(self, name: str) -> Any:
        """Delegate graph queries during the compatibility migration."""
        graph = self.__dict__.get("graph")
        if graph is not None:
            return getattr(graph, name)
        raise AttributeError(name)

    def supports(self, capability: Capability | str, language: str | None = None) -> bool:
        """Return whether all relevant frontends support a capability."""
        targets = [language] if language else list(self.languages)
        if not targets:
            return False
        return all(
            self.capabilities.get(target, CapabilitySet.from_values(target)).supports(capability)
            for target in targets
        )

    def missing_capabilities(
        self,
        required: Iterable[Capability | str],
        language: str | None = None,
    ) -> dict[str, list[str]]:
        targets = [language] if language else list(self.languages)
        return {
            target: self.capabilities.get(target, CapabilitySet.from_values(target)).missing(required)
            for target in targets
            if self.capabilities.get(target, CapabilitySet.from_values(target)).missing(required)
        }

    def read_source(self, file_path: str, max_bytes: int = 2 * 1024 * 1024) -> str | None:
        """Read a project file through the IR's bounded source repository."""
        root = Path(self.project_path).resolve() if self.project_path else None
        if root is None:
            return None
        resolved = resolve_project_file(root, file_path)
        if resolved is None:
            return None
        return read_text_bounded(resolved, max_bytes)

    def add_fact(self, fact: SemanticFact) -> None:
        self.facts.append(fact)

    def merge(self, other: "SemanticIR") -> "SemanticIR":
        merged_graph = self.graph.merge(other.graph)
        capability_map = dict(self.capabilities)
        capability_map.update(other.capabilities)
        units = dict(self.source_units)
        units.update(other.source_units)
        languages = tuple(sorted(set(self.languages) | set(other.languages)))
        facts = list(self.facts)
        for fact in other.facts:
            if fact not in facts:
                facts.append(fact)
        merged_graph.meta["semantic_ir_version"] = self.schema_version
        merged_graph.meta["languages"] = list(languages)
        return SemanticIR(
            graph=merged_graph,
            project_path=self.project_path or other.project_path,
            languages=languages,
            capabilities=capability_map,
            source_units=units,
            facts=facts,
            schema_version=self.schema_version,
        )

    def to_dict(self, include_facts: bool = True) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_path": self.project_path,
            "languages": list(self.languages),
            "capabilities": {
                language: capabilities.to_dict()
                for language, capabilities in sorted(self.capabilities.items())
            },
            "source_units": {
                path: unit.to_dict() for path, unit in sorted(self.source_units.items())
            },
            "facts": [fact.to_dict() for fact in self.facts] if include_facts else [],
            "graph": self.graph.to_dict(),
        }
