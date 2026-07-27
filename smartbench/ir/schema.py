"""Canonical semantic IR that separates language frontends from analyzers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from smartbench.graph.schema import CodeGraph
from smartbench.ir.capabilities import Capability, CapabilityLevel, CapabilitySet
from smartbench.ir.contracts import CONTRACT_SCHEMA_VERSION, validate_semantic_ir
from smartbench.ir.evidence import SemanticFact
from smartbench.ir.operations import OperationEdge, OperationEdgeKind, SemanticOperation
from smartbench.path_safety import read_text_bounded, read_text_prefix, resolve_project_file
from smartbench.provenance import (
    RepositoryZone,
    SourceRole,
    classify_repository_zone,
    classify_source_role,
)


@dataclass(frozen=True)
class SourceUnit:
    """Source-file metadata kept in the IR without eagerly duplicating content."""

    file_path: str
    language: str
    line_count: int = 0
    content_hash: str = ""
    role: SourceRole = SourceRole.PRODUCTION
    role_reason: str = "default source path"
    repository_zone: RepositoryZone = RepositoryZone.FIRST_PARTY
    repository_zone_reason: str = "default repository ownership"

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "line_count": self.line_count,
            "content_hash": self.content_hash,
            "role": self.role.value,
            "role_reason": self.role_reason,
            "repository_zone": self.repository_zone.value,
            "repository_zone_reason": self.repository_zone_reason,
        }


@dataclass(frozen=True)
class AnalysisAssessment:
    """Result of evaluating one rule's semantic contract."""

    status: CapabilityLevel
    languages: tuple[str, ...]
    requirements: dict[str, str]
    by_language: dict[str, dict[str, object]]
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "languages": list(self.languages),
            "requirements": dict(self.requirements),
            "by_language": self.by_language,
            "reason": self.reason,
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
    operations: list[SemanticOperation] = field(default_factory=list)
    operation_edges: list[OperationEdge] = field(default_factory=list)
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
            if not node.file_path or node.file_path in units:
                continue
            prefix = None
            if root:
                resolved = resolve_project_file(Path(root).resolve(), node.file_path)
                if resolved is not None:
                    prefix = read_text_prefix(resolved, 4 * 1024)
            role, role_reason = classify_source_role(node.file_path, prefix)
            zone, zone_reason = classify_repository_zone(node.file_path, prefix, role)
            units[node.file_path] = SourceUnit(
                file_path=node.file_path,
                language=node.language or language or "unknown",
                role=role,
                role_reason=role_reason,
                repository_zone=zone,
                repository_zone_reason=zone_reason,
            )
        capability_map = dict(capabilities and {capabilities.language: capabilities} or {})
        for detected in languages:
            capability_map.setdefault(
                detected,
                CapabilitySet.from_values(
                    detected,
                    [Capability.STRUCTURE, Capability.SOURCE_LOCATIONS, Capability.SYMBOLS],
                    partial={
                        Capability.CALL_GRAPH: "derived from structural graph; resolution may be heuristic"
                    },
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
            target: self.capabilities.get(target, CapabilitySet.from_values(target)).missing(
                required
            )
            for target in targets
            if self.capabilities.get(target, CapabilitySet.from_values(target)).missing(required)
        }

    def assess_requirements(
        self,
        required: Mapping[Capability | str, CapabilityLevel | str],
        languages: Iterable[str] | None = None,
    ) -> AnalysisAssessment:
        """Evaluate a rule contract without silently collapsing partial data.

        ``languages`` is the rule's applicability set.  This is important for
        mixed-language repositories: a Python rule must not be blocked merely
        because an unrelated Rust frontend is structural-only.
        """
        targets = tuple(sorted(set(languages or self.languages)))
        normalized_requirements: dict[str, CapabilityLevel] = {}
        for capability, minimum in required.items():
            key = capability.value if isinstance(capability, Capability) else str(capability)
            normalized_requirements[key] = (
                minimum if isinstance(minimum, CapabilityLevel) else CapabilityLevel(minimum)
            )
        if not targets:
            return AnalysisAssessment(
                status=CapabilityLevel.UNSUPPORTED,
                languages=(),
                requirements={key: value.value for key, value in normalized_requirements.items()},
                by_language={},
                reason="no applicable frontend language",
            )

        by_language: dict[str, dict[str, object]] = {}
        overall = CapabilityLevel.FULL
        reasons: list[str] = []
        for language in targets:
            capability_set = self.capabilities.get(language, CapabilitySet.from_values(language))
            assessment = capability_set.assess(normalized_requirements)
            status = CapabilityLevel(str(assessment["status"]))
            by_language[language] = assessment
            if status.rank < overall.rank:
                overall = status
            if status == CapabilityLevel.PARTIAL:
                for capability, detail in assessment["capabilities"].items():
                    if isinstance(detail, dict) and detail.get("reason"):
                        reasons.append(f"{language}.{capability}: {detail['reason']}")
            elif status == CapabilityLevel.UNSUPPORTED:
                reasons.append(f"{language}: required semantic capability is unavailable")
            elif status == CapabilityLevel.UNKNOWN:
                reasons.append(
                    f"{language}: rule declares no semantic capability requirements"
                )
        return AnalysisAssessment(
            status=overall,
            languages=targets,
            requirements={key: value.value for key, value in normalized_requirements.items()},
            by_language=by_language,
            reason="; ".join(dict.fromkeys(reasons)),
        )

    def source_units_for_roles(
        self,
        roles: Iterable[SourceRole] | None = None,
    ) -> tuple[SourceUnit, ...]:
        """Return source units in deterministic path order, optionally scoped."""
        allowed = set(roles) if roles is not None else None
        units = sorted(self.source_units.values(), key=lambda unit: unit.file_path)
        if allowed is None:
            return tuple(units)
        return tuple(unit for unit in units if unit.role in allowed)

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
        known_facts = {fact.fact_id for fact in facts}
        for fact in other.facts:
            if fact.fact_id not in known_facts:
                facts.append(fact)
                known_facts.add(fact.fact_id)
        operations = list(self.operations)
        known_operations = {operation.id for operation in operations}
        for operation in other.operations:
            if operation.id not in known_operations:
                operations.append(operation)
                known_operations.add(operation.id)
        operation_edges = list(self.operation_edges)
        known_edges = {_operation_edge_key(edge) for edge in operation_edges}
        for edge in other.operation_edges:
            if _operation_edge_key(edge) not in known_edges:
                operation_edges.append(edge)
                known_edges.add(_operation_edge_key(edge))
        merged_graph.meta["semantic_ir_version"] = self.schema_version
        merged_graph.meta["languages"] = list(languages)
        contract_errors = validate_semantic_ir(operations)
        merged_graph.meta["semantic_contract"] = {
            "version": CONTRACT_SCHEMA_VERSION,
            "valid": not contract_errors,
            "errors": list(contract_errors),
        }
        return SemanticIR(
            graph=merged_graph,
            project_path=self.project_path or other.project_path,
            languages=languages,
            capabilities=capability_map,
            source_units=units,
            facts=facts,
            operations=operations,
            operation_edges=operation_edges,
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
            "operations": [operation.to_dict() for operation in self.operations],
            "operation_edges": [edge.to_dict() for edge in self.operation_edges],
            "graph": self.graph.to_dict(),
        }


def _operation_edge_key(edge: OperationEdge) -> tuple[str, str, str, str]:
    """Return a stable, hashable identity for an edge's merge semantics."""
    kind = edge.kind.value if isinstance(edge.kind, OperationEdgeKind) else str(edge.kind)
    attributes = json.dumps(
        dict(edge.attributes), ensure_ascii=False, sort_keys=True, default=str
    )
    return edge.source_id, edge.target_id, kind, attributes
