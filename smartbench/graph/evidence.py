"""Deterministic graph retrieval for evidence-constrained agents."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from smartbench.graph.retriever import GraphRetriever
from smartbench.graph.schema import CodeGraph, EdgeType
from smartbench.ir import (
    EvidencePack,
    EvidenceRef,
    FactKind,
    SemanticFact,
    SemanticIR,
)

_EDGE_FACTS = {
    EdgeType.CONTAINS: FactKind.DEFINES,
    EdgeType.CALLS: FactKind.CALLS,
    EdgeType.IMPORTS: FactKind.IMPORTS,
    EdgeType.INHERITS: FactKind.CONTROLS,
    EdgeType.IMPLEMENTS: FactKind.CONTROLS,
    EdgeType.REFERENCES: FactKind.REFERENCES,
    EdgeType.RETURNS: FactKind.REFERENCES,
    EdgeType.DECORATES: FactKind.CONTROLS,
    EdgeType.ANNOTATES: FactKind.CONTROLS,
}


class DeterministicGraphRAG:
    """Retrieve a bounded, source-backed evidence subgraph.

    Retrieval is deterministic: the graph snapshot, lexical seed ranking,
    traversal, node ordering and evidence references are all stable.  An LLM
    may later interpret the returned ``EvidencePack`` but cannot change the
    underlying facts.
    """

    def __init__(self, ir: SemanticIR | CodeGraph, project_path: str = "", max_tokens: int = 4000):
        self.ir = ir if isinstance(ir, SemanticIR) else SemanticIR.from_graph(ir, project_path=project_path)
        root = self.ir.project_path or project_path
        self.retriever = GraphRetriever(self.ir.graph, root, max_tokens_estimate=max_tokens)

    def retrieve(self, query: str, hops: int = 2, max_nodes: int = 15) -> EvidencePack:
        """Return deterministic facts and source references for ``query``."""
        normalized_query = str(query)
        operation_matches = self._operation_matches(normalized_query, max_nodes)
        semantic_matches = self._semantic_fact_matches(normalized_query, max_nodes)
        seeds = self._file_seeds(normalized_query)
        if not seeds:
            seeds = self.retriever._find_seeds(normalized_query)
        trace = [f"query:{normalized_query}"]
        if not seeds and not operation_matches and not semantic_matches:
            trace.append("seeds:none")
            return EvidencePack.from_facts(
                normalized_query,
                (),
                retrieval_trace=trace,
                graph_version=self.graph_version(),
            )

        seed_label = ",".join(node.id for node in seeds)
        if not seed_label:
            seed_label = "operations-only" if operation_matches else "semantic-facts-only"
        trace.append("seeds:" + seed_label)
        subgraph = self.ir.graph.expand(
            [node.id for node in seeds],
            hops=max(0, int(hops)),
            edge_types={EdgeType.CALLS, EdgeType.CONTAINS, EdgeType.REFERENCES},
            direction="both",
        ) if seeds else CodeGraph()
        ranked = self.retriever._rank_nodes(subgraph, seeds, normalized_query) if seeds else []
        selected = self._select_nodes(
            ranked,
            seeds,
            normalized_query,
            max(0, int(max_nodes)),
        )
        selected_ids = {node.id for node in selected}
        trace.append(f"expand:{max(0, int(hops))}")
        trace.append("selected:" + ",".join(node.id for node in selected))
        if operation_matches:
            trace.append("operations:" + ",".join(operation.id for operation in operation_matches))
        if semantic_matches:
            trace.append("semantic_facts:" + ",".join(fact.fact_id for fact in semantic_matches))

        facts: list[SemanticFact] = []
        for node in selected:
            facts.append(
                SemanticFact(
                    subject=node.file_path,
                    predicate=FactKind.DEFINES,
                    object=node.id,
                    evidence=(self._node_ref(node),),
                    attributes={
                        "name": node.name,
                        "node_type": node.node_type.value,
                        "language": node.language,
                    },
                )
            )

        for edge in sorted(
            subgraph.edges,
            key=lambda item: (item.source_id, item.target_id, item.edge_type.value),
        ):
            if edge.source_id not in selected_ids or edge.target_id not in selected_ids:
                continue
            source = self.ir.graph.nodes.get(edge.source_id)
            target = self.ir.graph.nodes.get(edge.target_id)
            if source is None or target is None:
                continue
            predicate = _EDGE_FACTS.get(edge.edge_type, edge.edge_type.value)
            facts.append(
                SemanticFact(
                    subject=source.id,
                    predicate=predicate,
                    object=target.id,
                    evidence=(self._node_ref(source), self._node_ref(target)),
                    attributes={"edge_type": edge.edge_type.value},
                )
            )

        for operation in operation_matches:
            facts.append(
                SemanticFact(
                    subject=operation.scope_id or operation.location.file_path,
                    predicate=FactKind.STATE_TRANSITION,
                    object=operation.target or operation.value or operation.kind.value,
                    evidence=(operation.location,),
                    attributes={
                        "operation_id": operation.id,
                        "operation_kind": operation.kind.value,
                        "operands": list(operation.operands),
                        **dict(operation.attributes),
                    },
                )
            )

        known_fact_ids = {fact.fact_id for fact in facts}
        for fact in semantic_matches:
            if fact.fact_id not in known_fact_ids:
                facts.append(fact)
                known_fact_ids.add(fact.fact_id)

        return EvidencePack.from_facts(
            normalized_query,
            facts,
            retrieval_trace=trace,
            graph_version=self.graph_version(),
        )

    def render(self, pack: EvidencePack) -> str:
        """Render a pack for a prompt while retaining fact provenance."""
        lines = [
            f"<!-- SmartBench EvidencePack graph={pack.graph_version} query={pack.query!r} -->",
        ]
        for index, fact in enumerate(pack.facts, start=1):
            predicate = fact.predicate.value if isinstance(fact.predicate, FactKind) else fact.predicate
            lines.append(f"[fact-{index}] {fact.subject} --{predicate}--> {fact.object}")
            for ref in fact.evidence:
                location = f"{ref.file_path}:{ref.line_start}-{ref.line_end}"
                lines.append(f"  evidence: {location}")
                if ref.snippet:
                    lines.append(f"  {ref.snippet.strip()}")
        return "\n".join(lines)

    def graph_version(self) -> str:
        """Stable content address for the graph snapshot used in retrieval."""
        payload = {
            "schema_version": self.ir.schema_version,
            "graph": self.ir.graph.to_dict(),
            "operations": [operation.to_dict() for operation in self.ir.operations],
            "operation_edges": [edge.to_dict() for edge in self.ir.operation_edges],
            "facts": [fact.to_dict() for fact in self.ir.facts],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def _node_ref(self, node: Any) -> EvidenceRef:
        snippet = self._snippet(node.file_path, node.line_start, node.line_end)
        return EvidenceRef(
            file_path=node.file_path,
            line_start=max(1, node.line_start),
            line_end=max(1, node.line_end or node.line_start),
            snippet=snippet,
            source="code_graph",
        )

    def _file_seeds(self, query: str) -> list[Any]:
        """Prefer exact files while keeping multi-file queries balanced."""
        candidates = [
            node
            for node in self.ir.graph.nodes.values()
            if node.file_path and node.file_path in query
        ]
        by_path: dict[str, list[Any]] = {}
        for node in candidates:
            by_path.setdefault(node.file_path, []).append(node)
        query_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", query)
            if len(token) >= 3
        }

        def relevance(node: Any) -> tuple[Any, ...]:
            name = node.name.lower()
            matches = sum(token in name for token in query_tokens)
            type_priority = {
                "function": 0,
                "class": 1,
                "file": 2,
            }.get(node.node_type.value, 3)
            degree = (
                len(self.ir.graph._adj_out.get(node.id, ()))
                + len(self.ir.graph._adj_in.get(node.id, ()))
            )
            return (
                -matches,
                type_priority,
                -degree,
                node.line_start,
                node.name,
            )

        for nodes in by_path.values():
            nodes.sort(key=relevance)

        balanced: list[Any] = []
        paths = sorted(by_path)
        while len(balanced) < 10:
            added = False
            for path in paths:
                nodes = by_path[path]
                if nodes:
                    balanced.append(nodes.pop(0))
                    added = True
                    if len(balanced) == 10:
                        break
            if not added:
                break
        return balanced

    @staticmethod
    def _select_nodes(
        ranked: list[Any],
        seeds: list[Any],
        query: str,
        limit: int,
    ) -> list[Any]:
        """Reserve evidence capacity for every explicitly named source file."""
        if limit <= 0:
            return []
        explicit_paths = sorted({
            node.file_path
            for node in seeds
            if node.file_path and node.file_path in query
        })
        if len(explicit_paths) <= 1:
            return ranked[:limit]

        quota = max(1, limit // len(explicit_paths))
        selected: list[Any] = []
        selected_ids: set[str] = set()
        for path in explicit_paths:
            path_count = 0
            for node in (
                candidate for candidate in ranked
                if candidate.file_path == path
            ):
                if path_count >= quota:
                    break
                selected.append(node)
                selected_ids.add(node.id)
                path_count += 1

        for node in ranked:
            if len(selected) >= limit:
                break
            if node.id not in selected_ids:
                selected.append(node)
                selected_ids.add(node.id)
        return selected[:limit]

    def _operation_matches(self, query: str, limit: int) -> list[Any]:
        """Retrieve normalized operations when graph names are insufficient."""
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", query)
            if len(token) >= 3
        }
        scored: list[tuple[int, Any]] = []
        for operation in self.ir.operations:
            haystack = " ".join(
                [
                    operation.target,
                    operation.value,
                    " ".join(operation.operands),
                    json.dumps(dict(operation.attributes), ensure_ascii=False),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, operation))
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].location.file_path,
                item[1].location.line_start,
                item[1].id,
            )
        )
        return [operation for _, operation in scored[: max(0, int(limit))]]

    def _semantic_fact_matches(self, query: str, limit: int) -> list[SemanticFact]:
        """Retrieve frontend/linker facts with the same deterministic ranking."""
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", query)
            if len(token) >= 3
        }
        scored: list[tuple[int, SemanticFact]] = []
        for fact in self.ir.facts:
            predicate = fact.predicate.value if isinstance(fact.predicate, FactKind) else fact.predicate
            haystack = " ".join([
                fact.subject,
                str(predicate),
                fact.object,
                json.dumps(dict(fact.attributes), ensure_ascii=False, default=str),
            ]).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, fact))
        scored.sort(key=lambda item: (-item[0], item[1].fact_id))
        return [fact for _, fact in scored[: max(0, int(limit))]]

    def _snippet(self, file_path: str, line_start: int, line_end: int) -> str:
        source = self.ir.read_source(file_path)
        if not source:
            return ""
        lines = source.splitlines()
        start = max(0, line_start - 1)
        end = min(len(lines), max(start + 1, line_end))
        return "\n".join(lines[start:end])
