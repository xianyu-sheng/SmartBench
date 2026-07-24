"""Deterministic graph retrieval for evidence-constrained agents."""

from __future__ import annotations

import hashlib
import json
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
        seeds = self._file_seeds(normalized_query)
        if not seeds:
            seeds = self.retriever._find_seeds(normalized_query)
        trace = [f"query:{normalized_query}"]
        if not seeds:
            trace.append("seeds:none")
            return EvidencePack.from_facts(
                normalized_query,
                (),
                retrieval_trace=trace,
                graph_version=self.graph_version(),
            )

        trace.append("seeds:" + ",".join(node.id for node in seeds))
        subgraph = self.ir.graph.expand(
            [node.id for node in seeds],
            hops=max(0, int(hops)),
            edge_types={EdgeType.CALLS, EdgeType.CONTAINS, EdgeType.REFERENCES},
            direction="both",
        )
        ranked = self.retriever._rank_nodes(subgraph, seeds, normalized_query)
        selected = ranked[: max(0, int(max_nodes))]
        selected_ids = {node.id for node in selected}
        trace.append(f"expand:{max(0, int(hops))}")
        trace.append("selected:" + ",".join(node.id for node in selected))

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
        payload = self.ir.graph.to_dict()
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
        """Prefer an exact file mentioned by a finding over lexical noise."""
        candidates = [
            node
            for node in self.ir.graph.nodes.values()
            if node.file_path and node.file_path in query
        ]
        return sorted(
            candidates,
            key=lambda node: (len(node.file_path), node.file_path, node.name),
            reverse=True,
        )[:10]

    def _snippet(self, file_path: str, line_start: int, line_end: int) -> str:
        source = self.ir.read_source(file_path)
        if not source:
            return ""
        lines = source.splitlines()
        start = max(0, line_start - 1)
        end = min(len(lines), max(start + 1, line_end))
        return "\n".join(lines[start:end])
