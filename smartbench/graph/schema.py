"""
Code graph schema — language-agnostic graph model for code structure.

The graph represents code as nodes (functions, classes, files, modules)
connected by edges (calls, imports, contains, inherits).

This schema is intentionally generic. Language-specific details are
captured in node/edge properties, not in the graph structure itself.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class NodeType(Enum):
    """Types of nodes in the code graph."""
    FILE = "file"              # source file
    MODULE = "module"          # package / namespace
    CLASS = "class"            # class / struct / interface / trait
    FUNCTION = "function"      # function / method / subroutine
    VARIABLE = "variable"      # global / module-level variable
    IMPORT = "import"          # imported symbol (points to its definition)
    ANNOTATION = "annotation"  # decorator / attribute / annotation


class EdgeType(Enum):
    """Types of edges (relationships) in the code graph."""
    CONTAINS = "contains"      # file contains function, class contains method
    CALLS = "calls"            # function A calls function B
    IMPORTS = "imports"        # file A imports module B
    INHERITS = "inherits"      # class A extends class B
    IMPLEMENTS = "implements"  # class A implements interface B
    REFERENCES = "references"  # function A references variable B
    RETURNS = "returns"        # function returns type
    DECORATES = "decorates"    # decorator decorates function
    ANNOTATES = "annotates"    # annotation on class/method


@dataclass
class CodeNode:
    """A node in the code graph representing a code entity."""
    id: str                              # unique node ID
    node_type: NodeType
    name: str                            # human-readable name (function name, class name, etc.)
    file_path: str                       # relative path to the source file
    line_start: int = 0                  # starting line number (1-based)
    line_end: int = 0                    # ending line number
    language: str = ""                   # "go", "python", "rust", etc.
    properties: Dict[str, Any] = field(default_factory=dict)
    # Example properties: visibility, is_async, is_exported, signature, docstring

    @staticmethod
    def make_id(file_path: str, name: str, node_type: NodeType, line: int = 0) -> str:
        """Generate a deterministic node ID."""
        raw = f"{file_path}:{node_type.value}:{name}:{line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "node_type": self.node_type.value,
            "name": self.name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "language": self.language,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "CodeNode":
        return cls(
            id=d["id"],
            node_type=NodeType(d["node_type"]),
            name=d["name"],
            file_path=d["file_path"],
            line_start=d.get("line_start", 0),
            line_end=d.get("line_end", 0),
            language=d.get("language", ""),
            properties=d.get("properties", {}),
        )


@dataclass
class CodeEdge:
    """An edge in the code graph representing a relationship."""
    source_id: str
    target_id: str
    edge_type: EdgeType
    properties: Dict[str, Any] = field(default_factory=dict)
    # Example properties: call_count, line_number, is_conditional

    def to_dict(self) -> Dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "CodeEdge":
        return cls(
            source_id=d["source_id"],
            target_id=d["target_id"],
            edge_type=EdgeType(d["edge_type"]),
            properties=d.get("properties", {}),
        )


@dataclass
class CodeGraph:
    """
    A language-agnostic graph representation of a codebase.

    Can be queried for:
      - Call chains: given a function, find all callers / callees
      - Dependency paths: how does module A depend on module B?
      - Impact analysis: what breaks if function X changes?
      - Neighbor expansion: N-hop subgraph around a seed node
    """
    nodes: Dict[str, CodeNode] = field(default_factory=dict)
    edges: List[CodeEdge] = field(default_factory=list)
    # Adjacency for fast traversal
    _adj_out: Dict[str, List[CodeEdge]] = field(default_factory=dict, repr=False)
    _adj_in: Dict[str, List[CodeEdge]] = field(default_factory=dict, repr=False)
    meta: Dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: CodeNode) -> None:
        self.nodes[node.id] = node
        if node.id not in self._adj_out:
            self._adj_out[node.id] = []
        if node.id not in self._adj_in:
            self._adj_in[node.id] = []

    def add_edge(self, edge: CodeEdge) -> None:
        self.edges.append(edge)
        self._adj_out.setdefault(edge.source_id, []).append(edge)
        self._adj_in.setdefault(edge.target_id, []).append(edge)

    # ── Queries ──────────────────────────────────────────────────────

    def get_callers(self, node_id: str) -> List[CodeNode]:
        """Find all functions that call this node."""
        caller_ids = {
            e.source_id for e in self._adj_in.get(node_id, [])
            if e.edge_type == EdgeType.CALLS
        }
        return [self.nodes[nid] for nid in caller_ids if nid in self.nodes]

    def get_callees(self, node_id: str) -> List[CodeNode]:
        """Find all functions called by this node."""
        callee_ids = {
            e.target_id for e in self._adj_out.get(node_id, [])
            if e.edge_type == EdgeType.CALLS
        }
        return [self.nodes[nid] for nid in callee_ids if nid in self.nodes]

    def expand(self, seed_ids: List[str], hops: int = 2,
               edge_types: Optional[Set[EdgeType]] = None,
               direction: str = "both") -> "CodeGraph":
        """
        Return a subgraph by expanding from seed nodes.

        Args:
            seed_ids: Starting node IDs
            hops: Number of hops to expand
            edge_types: Which edge types to follow (None = all)
            direction: "out" (follow outgoing), "in" (incoming), "both"

        Returns:
            A new CodeGraph containing only the expanded subgraph.
        """
        visited: Set[str] = set()
        frontier: Set[str] = set(seed_ids)
        subgraph = CodeGraph()

        for _ in range(hops):
            next_frontier: Set[str] = set()
            for node_id in frontier:
                if node_id in visited:
                    continue
                visited.add(node_id)
                if node_id in self.nodes:
                    subgraph.add_node(self.nodes[node_id])

                # Follow outgoing edges
                if direction in ("out", "both"):
                    for edge in self._adj_out.get(node_id, []):
                        if edge_types and edge.edge_type not in edge_types:
                            continue
                        subgraph.add_edge(edge)
                        next_frontier.add(edge.target_id)

                # Follow incoming edges
                if direction in ("in", "both"):
                    for edge in self._adj_in.get(node_id, []):
                        if edge_types and edge.edge_type not in edge_types:
                            continue
                        subgraph.add_edge(edge)
                        next_frontier.add(edge.source_id)

            frontier = next_frontier - visited

        # Add any nodes referenced by edges that weren't expanded to
        for edge in subgraph.edges:
            for nid in (edge.source_id, edge.target_id):
                if nid not in subgraph.nodes and nid in self.nodes:
                    subgraph.add_node(self.nodes[nid])

        return subgraph

    def find_by_name(self, name: str, node_type=None) -> List[CodeNode]:
        """Simple name-based search. For fuzzy matching use GraphRetriever.

        Args:
            name: Name substring to search for
            node_type: NodeType enum value OR string (e.g. 'function') to filter by type
        """
        results = []
        name_lower = name.lower()
        # Normalize node_type: accept both enum and string
        target_type = node_type
        if isinstance(node_type, str):
            try:
                target_type = NodeType(node_type)
            except ValueError:
                target_type = None

        for node in self.nodes.values():
            if name_lower in node.name.lower():
                if target_type is None or node.node_type == target_type:
                    results.append(node)
        return results

    def merge(self, other: "CodeGraph") -> "CodeGraph":
        """Merge another graph into this one (non-destructive, returns new graph).

        Duplicate nodes (same ID) are skipped; all edges from both graphs included.
        """
        merged = CodeGraph(meta={**self.meta, "merged_languages": True})
        for nid, node in self.nodes.items():
            merged.add_node(node)
        for edge in self.edges:
            merged.add_edge(edge)
        for nid, node in other.nodes.items():
            if nid not in merged.nodes:
                merged.add_node(node)
        for edge in other.edges:
            merged.add_edge(edge)
        return merged

    def summary(self) -> str:
        """One-line graph summary."""
        n_files = sum(1 for n in self.nodes.values() if n.node_type == NodeType.FILE)
        n_funcs = sum(1 for n in self.nodes.values() if n.node_type == NodeType.FUNCTION)
        n_classes = sum(1 for n in self.nodes.values() if n.node_type == NodeType.CLASS)
        n_calls = sum(1 for e in self.edges if e.edge_type == EdgeType.CALLS)
        return (f"Graph: {len(self.nodes)} nodes ({n_files} files, {n_funcs} funcs, "
                f"{n_classes} classes), {len(self.edges)} edges ({n_calls} calls)")

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "CodeGraph":
        graph = cls(meta=d.get("meta", {}))
        for nid, nd in d.get("nodes", {}).items():
            node = CodeNode.from_dict(nd)
            graph.nodes[nid] = node
            graph._adj_out.setdefault(nid, [])
            graph._adj_in.setdefault(nid, [])
        for ed in d.get("edges", []):
            edge = CodeEdge.from_dict(ed)
            graph.edges.append(edge)
            graph._adj_out.setdefault(edge.source_id, []).append(edge)
            graph._adj_in.setdefault(edge.target_id, []).append(edge)
        return graph

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, s: str) -> "CodeGraph":
        return cls.from_dict(json.loads(s))
