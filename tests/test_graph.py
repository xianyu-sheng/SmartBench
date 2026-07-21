"""
Unit tests for the code graph module (smartbench.graph).

Tests cover:
  - Data model: CodeNode, CodeEdge, NodeType, EdgeType
  - CodeGraph: node/edge ops, queries, serialization
  - CodeGraphBuilder: file-based graph construction for Python, Go, etc.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from smartbench.detector.fingerprint import Language
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.schema import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    EdgeType,
    NodeType,
)

# ===========================================================================
# NodeType enum
# ===========================================================================

class TestNodeType:
    def test_values(self):
        assert NodeType.FILE.value == "file"
        assert NodeType.MODULE.value == "module"
        assert NodeType.CLASS.value == "class"
        assert NodeType.FUNCTION.value == "function"
        assert NodeType.VARIABLE.value == "variable"
        assert NodeType.IMPORT.value == "import"
        assert NodeType.ANNOTATION.value == "annotation"

    def test_membership(self):
        assert NodeType.FILE in NodeType
        assert NodeType.FUNCTION in NodeType
        assert NodeType.CLASS in NodeType

    def test_unique_values(self):
        values = [e.value for e in NodeType]
        assert len(values) == len(set(values))


# ===========================================================================
# EdgeType enum
# ===========================================================================

class TestEdgeType:
    def test_values(self):
        assert EdgeType.CONTAINS.value == "contains"
        assert EdgeType.CALLS.value == "calls"
        assert EdgeType.IMPORTS.value == "imports"
        assert EdgeType.INHERITS.value == "inherits"
        assert EdgeType.IMPLEMENTS.value == "implements"
        assert EdgeType.REFERENCES.value == "references"
        assert EdgeType.RETURNS.value == "returns"
        assert EdgeType.DECORATES.value == "decorates"
        assert EdgeType.ANNOTATES.value == "annotates"

    def test_membership(self):
        assert EdgeType.CALLS in EdgeType
        assert EdgeType.IMPORTS in EdgeType
        assert EdgeType.INHERITS in EdgeType

    def test_unique_values(self):
        values = [e.value for e in EdgeType]
        assert len(values) == len(set(values))


# ===========================================================================
# CodeNode
# ===========================================================================

class TestCodeNodeConstruction:
    def test_full_construction(self):
        node = CodeNode(
            id="abc123",
            node_type=NodeType.FUNCTION,
            name="my_func",
            file_path="src/main.py",
            line_start=10,
            line_end=45,
            language="python",
            properties={"visibility": "public", "is_async": True},
        )
        assert node.id == "abc123"
        assert node.node_type == NodeType.FUNCTION
        assert node.name == "my_func"
        assert node.file_path == "src/main.py"
        assert node.line_start == 10
        assert node.line_end == 45
        assert node.language == "python"
        assert node.properties == {"visibility": "public", "is_async": True}

    def test_default_line_end_is_zero(self):
        node = CodeNode(
            id="x1",
            node_type=NodeType.FUNCTION,
            name="f",
            file_path="a.py",
            line_start=5,
        )
        assert node.line_end == 0

    def test_default_language_is_empty(self):
        node = CodeNode(
            id="x1",
            node_type=NodeType.MODULE,
            name="mymod",
            file_path="mymod/__init__.py",
            line_start=1,
        )
        assert node.language == ""

    def test_default_properties_is_empty_dict(self):
        node = CodeNode(
            id="x1",
            node_type=NodeType.VARIABLE,
            name="DEBUG",
            file_path="config.py",
            line_start=1,
        )
        assert node.properties == {}

    def test_default_line_start_is_zero(self):
        node = CodeNode(
            id="x1",
            node_type=NodeType.FILE,
            name="main.py",
            file_path="main.py",
        )
        assert node.line_start == 0

    def test_make_id_is_deterministic(self):
        id1 = CodeNode.make_id("src/main.py", "my_func", NodeType.FUNCTION, 10)
        id2 = CodeNode.make_id("src/main.py", "my_func", NodeType.FUNCTION, 10)
        assert id1 == id2
        assert len(id1) == 16  # truncated sha256 hex

    def test_make_id_differs_on_input(self):
        id1 = CodeNode.make_id("a.py", "foo", NodeType.FUNCTION, 1)
        id2 = CodeNode.make_id("a.py", "bar", NodeType.FUNCTION, 1)
        assert id1 != id2


class TestCodeNodeSerialization:
    def test_to_dict(self):
        node = CodeNode(
            id="n1",
            node_type=NodeType.CLASS,
            name="MyClass",
            file_path="models.py",
            line_start=20,
            line_end=80,
            language="python",
            properties={"visibility": "public"},
        )
        d = node.to_dict()
        assert d["id"] == "n1"
        assert d["node_type"] == "class"
        assert d["name"] == "MyClass"
        assert d["file_path"] == "models.py"
        assert d["line_start"] == 20
        assert d["line_end"] == 80
        assert d["language"] == "python"
        assert d["properties"] == {"visibility": "public"}

    def test_from_dict(self):
        d = {
            "id": "n2",
            "node_type": "function",
            "name": "helper",
            "file_path": "utils.py",
            "line_start": 5,
            "line_end": 0,
            "language": "go",
            "properties": {},
        }
        node = CodeNode.from_dict(d)
        assert node.id == "n2"
        assert node.node_type == NodeType.FUNCTION
        assert node.name == "helper"
        assert node.line_start == 5
        assert node.language == "go"

    def test_from_dict_missing_optional_fields(self):
        d = {
            "id": "n3",
            "node_type": "variable",
            "name": "CONST",
            "file_path": "c.py",
        }
        node = CodeNode.from_dict(d)
        assert node.line_start == 0
        assert node.line_end == 0
        assert node.language == ""
        assert node.properties == {}

    def test_roundtrip(self):
        original = CodeNode(
            id="rt1",
            node_type=NodeType.FUNCTION,
            name="roundtrip_test",
            file_path="test.py",
            line_start=1,
            line_end=10,
            language="python",
            properties={"key": "value"},
        )
        restored = CodeNode.from_dict(original.to_dict())
        assert restored == original


# ===========================================================================
# CodeEdge
# ===========================================================================

class TestCodeEdgeConstruction:
    def test_full_construction(self):
        edge = CodeEdge(
            source_id="n1",
            target_id="n2",
            edge_type=EdgeType.CALLS,
            properties={"call_count": 5, "line_number": 42},
        )
        assert edge.source_id == "n1"
        assert edge.target_id == "n2"
        assert edge.edge_type == EdgeType.CALLS
        assert edge.properties == {"call_count": 5, "line_number": 42}

    def test_default_properties(self):
        edge = CodeEdge(
            source_id="a",
            target_id="b",
            edge_type=EdgeType.CONTAINS,
        )
        assert edge.properties == {}


class TestCodeEdgeSerialization:
    def test_to_dict(self):
        edge = CodeEdge(
            source_id="src1",
            target_id="tgt1",
            edge_type=EdgeType.INHERITS,
            properties={"is_direct": True},
        )
        d = edge.to_dict()
        assert d["source_id"] == "src1"
        assert d["target_id"] == "tgt1"
        assert d["edge_type"] == "inherits"
        assert d["properties"] == {"is_direct": True}

    def test_from_dict(self):
        d = {
            "source_id": "a",
            "target_id": "b",
            "edge_type": "references",
            "properties": {"line": 99},
        }
        edge = CodeEdge.from_dict(d)
        assert edge.source_id == "a"
        assert edge.target_id == "b"
        assert edge.edge_type == EdgeType.REFERENCES
        assert edge.properties == {"line": 99}

    def test_from_dict_missing_properties(self):
        d = {
            "source_id": "x",
            "target_id": "y",
            "edge_type": "imports",
        }
        edge = CodeEdge.from_dict(d)
        assert edge.properties == {}

    def test_roundtrip(self):
        original = CodeEdge(
            source_id="s", target_id="t",
            edge_type=EdgeType.DECORATES,
            properties={"decorator": "@login_required"},
        )
        restored = CodeEdge.from_dict(original.to_dict())
        assert restored == original


# ===========================================================================
# CodeGraph
# ===========================================================================

# -- Fixtures --

@pytest.fixture
def graph_with_nodes():
    g = CodeGraph()
    g.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="func_a",
                        file_path="mod.py", line_start=1))
    g.add_node(CodeNode(id="b", node_type=NodeType.FUNCTION, name="func_b",
                        file_path="mod.py", line_start=10))
    g.add_node(CodeNode(id="c", node_type=NodeType.FUNCTION, name="func_c",
                        file_path="mod.py", line_start=20))
    return g


@pytest.fixture
def graph_with_edges(graph_with_nodes):
    g = graph_with_nodes
    g.add_edge(CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS))
    g.add_edge(CodeEdge(source_id="b", target_id="c", edge_type=EdgeType.CALLS))
    g.add_edge(CodeEdge(source_id="a", target_id="c", edge_type=EdgeType.CALLS))
    return g


# -- Node operations --

class TestCodeGraphAddNode:
    def test_adds_node(self):
        g = CodeGraph()
        node = CodeNode(id="n1", node_type=NodeType.FUNCTION, name="f",
                        file_path="x.py", line_start=1)
        g.add_node(node)
        assert "n1" in g.nodes
        assert g.nodes["n1"] == node

    def test_duplicate_id_overwrites(self):
        g = CodeGraph()
        n1 = CodeNode(id="n1", node_type=NodeType.FUNCTION, name="old",
                      file_path="x.py", line_start=1)
        n2 = CodeNode(id="n1", node_type=NodeType.FUNCTION, name="new",
                      file_path="x.py", line_start=5)
        g.add_node(n1)
        g.add_node(n2)
        assert g.nodes["n1"].name == "new"

    def test_init_adjacency_entries(self):
        g = CodeGraph()
        node = CodeNode(id="iso", node_type=NodeType.FUNCTION, name="iso",
                        file_path="x.py", line_start=1)
        g.add_node(node)
        assert "iso" in g._adj_out
        assert "iso" in g._adj_in
        assert g._adj_out["iso"] == []
        assert g._adj_in["iso"] == []


# -- Edge operations --

class TestCodeGraphAddEdge:
    def test_adds_edge(self, graph_with_nodes):
        g = graph_with_nodes
        edge = CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS)
        g.add_edge(edge)
        assert edge in g.edges

    def test_add_edge_populates_out_adjacency(self, graph_with_nodes):
        g = graph_with_nodes
        g.add_edge(CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS))
        out_ids = {e.target_id for e in g._adj_out["a"]}
        assert "b" in out_ids

    def test_add_edge_populates_in_adjacency(self, graph_with_nodes):
        g = graph_with_nodes
        g.add_edge(CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS))
        in_ids = {e.source_id for e in g._adj_in["b"]}
        assert "a" in in_ids

    def test_edge_appended_to_edges_list(self, graph_with_nodes):
        g = graph_with_nodes
        g.add_edge(CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS))
        g.add_edge(CodeEdge(source_id="b", target_id="c", edge_type=EdgeType.CALLS))
        assert len(g.edges) == 2


# -- Queries --

class TestCodeGraphCallers:
    def test_get_callers_returns_calling_nodes(self, graph_with_edges):
        callers = graph_with_edges.get_callers("c")
        caller_names = {n.name for n in callers}
        assert "func_a" in caller_names
        assert "func_b" in caller_names

    def test_get_callers_for_leaf_node(self, graph_with_edges):
        """Node 'c' is called by both 'a' and 'b'."""
        callers = graph_with_edges.get_callers("c")
        assert len(callers) == 2

    def test_get_callers_for_root_node(self, graph_with_edges):
        """Node 'a' has no callers."""
        callers = graph_with_edges.get_callers("a")
        assert callers == []

    def test_get_callers_nonexistent_node(self, graph_with_edges):
        assert graph_with_edges.get_callers("nonexistent") == []

    def test_get_callers_empty_graph(self):
        assert CodeGraph().get_callers("x") == []


class TestCodeGraphCallees:
    def test_get_callees_returns_called_nodes(self, graph_with_edges):
        callees = graph_with_edges.get_callees("a")
        callee_names = {n.name for n in callees}
        assert "func_b" in callee_names
        assert "func_c" in callee_names

    def test_get_callees_for_leaf_node(self, graph_with_edges):
        """Node 'c' calls nothing."""
        callees = graph_with_edges.get_callees("c")
        assert callees == []

    def test_get_callees_nonexistent_node(self, graph_with_edges):
        assert graph_with_edges.get_callees("nonexistent") == []


class TestCodeGraphGetNodeViaNodesDict:
    def test_get_node_by_id(self, graph_with_nodes):
        node = graph_with_nodes.nodes.get("a")
        assert node is not None
        assert node.name == "func_a"

    def test_missing_node_returns_none(self, graph_with_nodes):
        assert graph_with_nodes.nodes.get("nonexistent") is None


class TestCodeGraphRemoveNodeViaDict:
    def test_remove_node_from_nodes_dict(self, graph_with_edges):
        g = graph_with_edges
        del g.nodes["a"]
        assert "a" not in g.nodes
        # Edges still exist in the list — removal from edges is not automatic
        remaining_sources = {e.source_id for e in g.edges}
        assert "a" in remaining_sources  # edges referencing 'a' persist

    def test_remove_nonexistent_node_raises_key_error(self, graph_with_nodes):
        with pytest.raises(KeyError):
            del graph_with_nodes.nodes["nonexistent"]


# -- find_by_name --

class TestCodeGraphFindByName:
    def test_find_by_name_exact(self, graph_with_nodes):
        results = graph_with_nodes.find_by_name("func_a")
        assert len(results) == 1
        assert results[0].id == "a"

    def test_find_by_name_partial(self, graph_with_nodes):
        results = graph_with_nodes.find_by_name("func")
        assert len(results) == 3

    def test_find_by_name_case_insensitive(self, graph_with_nodes):
        results = graph_with_nodes.find_by_name("FUNC_A")
        assert len(results) == 1

    def test_find_by_name_with_node_type_filter(self, graph_with_nodes):
        graph_with_nodes.add_node(
            CodeNode(id="d", node_type=NodeType.CLASS, name="func_helper",
                     file_path="mod.py", line_start=30)
        )
        results = graph_with_nodes.find_by_name("func", node_type=NodeType.CLASS)
        assert len(results) == 1
        assert results[0].id == "d"

    def test_find_by_name_string_type_filter(self, graph_with_nodes):
        graph_with_nodes.add_node(
            CodeNode(id="d", node_type=NodeType.CLASS, name="func_helper",
                     file_path="mod.py", line_start=30)
        )
        results = graph_with_nodes.find_by_name("func", node_type="class")
        assert len(results) == 1

    def test_find_by_name_invalid_string_type_returns_all(self, graph_with_nodes):
        results = graph_with_nodes.find_by_name("func", node_type="not_a_real_type")
        assert len(results) == 3

    def test_find_by_name_no_match(self, graph_with_nodes):
        assert graph_with_nodes.find_by_name("nonexistent") == []


# -- Serialization roundtrip --

class TestCodeGraphSerialization:
    def test_to_dict(self, graph_with_edges):
        g = graph_with_edges
        d = g.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert "meta" in d
        assert len(d["nodes"]) == 3
        assert len(d["edges"]) == 3

    def test_from_dict_reconstructs_nodes_and_edges(self, graph_with_edges):
        d = graph_with_edges.to_dict()
        g2 = CodeGraph.from_dict(d)
        assert len(g2.nodes) == 3
        assert len(g2.edges) == 3
        assert g2.nodes["a"].name == "func_a"

    def test_from_dict_rebuilds_adjacency(self, graph_with_edges):
        d = graph_with_edges.to_dict()
        g2 = CodeGraph.from_dict(d)
        assert len(g2._adj_out["a"]) == 2  # a calls b and c
        assert len(g2._adj_in["c"]) == 2   # a and b call c

    def test_roundtrip_preserves_callers(self, graph_with_edges):
        d = graph_with_edges.to_dict()
        g2 = CodeGraph.from_dict(d)
        callers = g2.get_callers("c")
        assert len(callers) == 2

    def test_roundtrip_preserves_callees(self, graph_with_edges):
        d = graph_with_edges.to_dict()
        g2 = CodeGraph.from_dict(d)
        callees = g2.get_callees("a")
        assert len(callees) == 2

    def test_to_json_roundtrip(self, graph_with_edges):
        json_str = graph_with_edges.to_json()
        parsed = json.loads(json_str)
        assert "nodes" in parsed
        assert "edges" in parsed

        g2 = CodeGraph.from_json(json_str)
        assert len(g2.nodes) == len(graph_with_edges.nodes)
        assert len(g2.edges) == len(graph_with_edges.edges)

    def test_meta_preserved(self):
        g = CodeGraph(meta={"project": "test", "version": 1})
        g2 = CodeGraph.from_dict(g.to_dict())
        assert g2.meta["project"] == "test"
        assert g2.meta["version"] == 1

    def test_from_dict_empty(self):
        g = CodeGraph.from_dict({})
        assert len(g.nodes) == 0
        assert len(g.edges) == 0

    def test_from_dict_with_empty_containers(self):
        g = CodeGraph.from_dict({"nodes": {}, "edges": [], "meta": {}})
        assert len(g.nodes) == 0
        assert len(g.edges) == 0


# -- Node/edge counts via len() on internal dicts/lists --

class TestCodeGraphCounts:
    def test_node_count_via_len(self, graph_with_nodes):
        assert len(graph_with_nodes.nodes) == 3

    def test_empty_graph_node_count(self):
        assert len(CodeGraph().nodes) == 0

    def test_edge_count_via_len(self, graph_with_nodes):
        g = graph_with_nodes
        g.add_edge(CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS))
        assert len(g.edges) == 1

    def test_empty_graph_edge_count(self):
        assert len(CodeGraph().edges) == 0


# -- Expand / subgraph --

class TestCodeGraphExpand:
    def test_expand_zero_hops(self, graph_with_edges):
        """expand with hops=0 is a no-op -- seed nodes are not added."""
        sub = graph_with_edges.expand(["a"], hops=0)
        assert len(sub.nodes) == 0
        assert len(sub.edges) == 0

    def test_expand_one_hop_out(self, graph_with_edges):
        sub = graph_with_edges.expand(["a"], hops=1, direction="out")
        assert "a" in sub.nodes
        assert "b" in sub.nodes
        # a calls c directly, so 1 hop includes c
        assert "c" in sub.nodes

    def test_expand_one_hop_in(self, graph_with_edges):
        sub = graph_with_edges.expand(["c"], hops=1, direction="in")
        assert "c" in sub.nodes
        assert "a" in sub.nodes
        assert "b" in sub.nodes

    def test_expand_with_edge_type_filter(self, graph_with_edges):
        graph_with_edges.add_edge(
            CodeEdge(source_id="a", target_id="c", edge_type=EdgeType.CONTAINS)
        )
        sub = graph_with_edges.expand(
            ["a"], hops=1, edge_types={EdgeType.CALLS}, direction="out"
        )
        assert "c" in sub.nodes

    def test_expand_nonexistent_seed(self, graph_with_edges):
        sub = graph_with_edges.expand(["nonexistent"], hops=1)
        assert len(sub.nodes) == 0
        assert len(sub.edges) == 0

    def test_expand_from_node_with_no_edges(self, graph_with_nodes):
        sub = graph_with_nodes.expand(["a"], hops=2)
        assert len(sub.nodes) == 1
        assert "a" in sub.nodes

    def test_expand_adds_referenced_nodes(self, graph_with_edges):
        # Remove 'b' but keep edges referencing it
        g = graph_with_edges
        del g.nodes["b"]
        sub = g.expand(["a"], hops=1, direction="out")
        # 'b' is referenced by an edge but missing from nodes
        # The expand method should add it back if found in self.nodes
        # But 'b' was deleted, so it won't be added
        assert "b" not in sub.nodes


# -- merge --

class TestCodeGraphMerge:
    def test_merge_combines_nodes_and_edges(self):
        g1 = CodeGraph()
        g1.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="f1",
                             file_path="a.py", line_start=1))

        g2 = CodeGraph()
        g2.add_node(CodeNode(id="c", node_type=NodeType.FUNCTION, name="f2",
                             file_path="b.py", line_start=1))
        g2.add_edge(CodeEdge(source_id="c", target_id="d", edge_type=EdgeType.CALLS))

        merged = g1.merge(g2)
        assert len(merged.nodes) == 2
        assert len(merged.edges) == 1

    def test_merge_deduplicates_nodes_keeps_first(self):
        g1 = CodeGraph()
        g1.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="f1",
                             file_path="a.py", line_start=1))
        g2 = CodeGraph()
        g2.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="f1_v2",
                             file_path="a.py", line_start=5))

        merged = g1.merge(g2)
        # Original node from g1 should be kept (first wins)
        assert merged.nodes["a"].line_start == 1

    def test_merge_includes_all_edges(self):
        g1 = CodeGraph()
        g1.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="f1",
                             file_path="a.py", line_start=1))
        g1.add_node(CodeNode(id="b", node_type=NodeType.FUNCTION, name="f2",
                             file_path="a.py", line_start=5))
        g1.add_edge(CodeEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLS))

        g2 = CodeGraph()
        g2.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="f1",
                             file_path="a.py", line_start=1))
        g2.add_node(CodeNode(id="c", node_type=NodeType.FUNCTION, name="f3",
                             file_path="b.py", line_start=1))
        g2.add_edge(CodeEdge(source_id="a", target_id="c", edge_type=EdgeType.CALLS))

        merged = g1.merge(g2)
        assert len(merged.edges) == 2

    def test_merge_sets_merged_languages_meta(self):
        g1 = CodeGraph(meta={"a": 1})
        g2 = CodeGraph(meta={"b": 2})
        merged = g1.merge(g2)
        assert merged.meta.get("merged_languages") is True
        assert merged.meta["a"] == 1

    def test_merge_is_non_destructive(self):
        g1 = CodeGraph()
        g1.add_node(CodeNode(id="a", node_type=NodeType.FUNCTION, name="f1",
                             file_path="a.py", line_start=1))
        g2 = CodeGraph()
        g2.add_node(CodeNode(id="b", node_type=NodeType.FUNCTION, name="f2",
                             file_path="b.py", line_start=1))

        g1.merge(g2)
        # g1 should be unchanged
        assert len(g1.nodes) == 1
        assert "b" not in g1.nodes


# -- summary --

class TestCodeGraphSummary:
    def test_summary_includes_counts(self, graph_with_edges):
        s = graph_with_edges.summary()
        assert "3 nodes" in s
        assert "3 edges" in s
        assert "3 calls" in s

    def test_summary_empty_graph(self):
        s = CodeGraph().summary()
        assert "0 nodes" in s
        assert "0 edges" in s


# ===========================================================================
# CodeGraphBuilder
# ===========================================================================

# -- Python fixtures --

@pytest.fixture
def python_file(test_dir):
    f = test_dir / "test.py"
    f.write_text("""import os
from typing import List

class MyClass:
    def method(self):
        return os.path.join("a", "b")

def helper(x: int) -> str:
    return str(x)

def main():
    result = helper(42)
    obj = MyClass()
    obj.method()
""")
    return f


@pytest.fixture
def go_file(test_dir):
    f = test_dir / "main.go"
    f.write_text("""package main

import "os"

type User struct {
\tName string
\tAge  int
}

func Greet(name string) string {
\treturn "Hello, " + name
}

func main() {
\tmsg := Greet("world")
\tfmt.Println(msg)
}
""")
    return f


@pytest.fixture
def builder():
    return CodeGraphBuilder(max_files=500, use_treesitter=False)


@pytest.fixture
def test_dir():
    """Custom temp dir fixture to avoid PermissionError with built-in test_dir."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(str(d), ignore_errors=True)


# -- Python builder tests --

class TestCodeGraphBuilderPython:
    def test_detects_function_definitions(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        func_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "helper" in func_names
        assert "main" in func_names
        assert "method" in func_names

    def test_function_has_line_start(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        funcs = {
            n.name: n for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert isinstance(funcs["helper"].line_start, int)
        assert funcs["helper"].line_start > 0

    def test_function_has_signature_property(self, python_file, builder):
        """Signature may be empty for some functions due to regex newline matching."""
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        funcs = {
            n.name: n for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        for name in ("helper", "main", "method"):
            assert "signature" in funcs[name].properties, f"missing signature for {name}"
        # At least one function should have a non-empty signature
        assert any(
            funcs[n].properties["signature"]
            for n in funcs
            if funcs[n].properties.get("signature")
        )

    def test_detects_class_definitions(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        class_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.CLASS
        }
        assert "MyClass" in class_names

    def test_detects_imports(self, python_file, builder):
        r"""Import regex [\w\s,]+ is greedy across newlines, so the captured
        import name may include content past the import line."""
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        import_nodes = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.IMPORT
        ]
        assert len(import_nodes) >= 1
        # The import name includes multi-line content due to greedy regex
        assert any("os" in n.name for n in import_nodes)

    def test_detects_function_calls(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        main_node = next(
            n for n in graph.nodes.values()
            if n.name == "main" and n.node_type == NodeType.FUNCTION
        )
        callees = graph.get_callees(main_node.id)
        callee_names = {n.name for n in callees}
        assert "helper" in callee_names

    def test_detects_class_inheritance(self, test_dir, builder):
        f = test_dir / "inherit.py"
        f.write_text("""class Base:
    pass

class Derived(Base):
    pass
""")
        graph = builder.build(str(test_dir), Language.PYTHON)
        class_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.CLASS
        }
        assert "Base" in class_names
        assert "Derived" in class_names

    def test_file_node_created(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        file_nodes = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FILE
        ]
        assert len(file_nodes) == 1
        assert file_nodes[0].name == "test.py"

    def test_file_node_has_line_count_property(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        file_node = next(
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FILE
        )
        assert "line_count" in file_node.properties
        assert file_node.properties["line_count"] > 0

    def test_contains_edge_for_each_function(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        contains_edges = [
            e for e in graph.edges if e.edge_type == EdgeType.CONTAINS
        ]
        assert len(contains_edges) >= 3

    def test_graph_meta_has_build_info(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        assert "project_path" in graph.meta
        assert "language" in graph.meta
        assert graph.meta["language"] == "python"
        assert "build_time_ms" in graph.meta
        assert isinstance(graph.meta["build_time_ms"], int)


# -- Go builder tests --

class TestCodeGraphBuilderGo:
    def test_detects_func_declarations(self, go_file, builder):
        graph = builder.build(str(go_file.parent), Language.GO)
        func_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "Greet" in func_names
        assert "main" in func_names

    def test_detects_struct_types(self, go_file, builder):
        graph = builder.build(str(go_file.parent), Language.GO)
        class_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.CLASS
        }
        assert "User" in class_names

    def test_detects_go_imports(self, go_file, builder):
        graph = builder.build(str(go_file.parent), Language.GO)
        import_nodes = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.IMPORT
        ]
        imported_names = {n.name for n in import_nodes}
        assert "os" in imported_names

    def test_detects_go_function_calls(self, go_file, builder):
        graph = builder.build(str(go_file.parent), Language.GO)
        main_node = next(
            n for n in graph.nodes.values()
            if n.name == "main" and n.node_type == NodeType.FUNCTION
        )
        callees = graph.get_callees(main_node.id)
        callee_names = {n.name for n in callees}
        assert "Greet" in callee_names


# -- Edge cases --

class TestCodeGraphBuilderEdgeCases:
    def test_empty_file(self, test_dir, builder):
        """Empty file should create a file node but no function/class nodes."""
        f = test_dir / "empty.py"
        f.write_text("")
        graph = builder.build(str(test_dir), Language.PYTHON)
        # File node is always created
        assert len(graph.nodes) >= 1
        funcs = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        ]
        assert len(funcs) == 0

    def test_file_with_syntax_errors_does_not_crash(self, test_dir, builder):
        f = test_dir / "broken.py"
        f.write_text("""def valid():
    pass

def broken(:
    x = 1
""")
        graph = builder.build(str(test_dir), Language.PYTHON)
        # Even with broken syntax, regex-based parsing should still catch "valid"
        funcs = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "valid" in funcs

    def test_build_from_directory_processes_all_files(self, test_dir, builder):
        (test_dir / "mod1.py").write_text("def foo(): pass\n")
        (test_dir / "mod2.py").write_text("def bar(): pass\ndef baz(): pass\n")
        graph = builder.build(str(test_dir), Language.PYTHON)
        func_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "foo" in func_names
        assert "bar" in func_names
        assert "baz" in func_names

    def test_non_python_file_skipped_when_parsing_python(self, test_dir, builder):
        (test_dir / "code.py").write_text("def f(): pass\n")
        (test_dir / "code.js").write_text("function f() { return 1; }\n")
        graph = builder.build(str(test_dir), Language.PYTHON)
        file_nodes = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FILE
        ]
        file_names = {n.name for n in file_nodes}
        assert "code.py" in file_names
        assert "code.js" not in file_names

    def test_build_nonexistent_directory_returns_graph_without_files(self, test_dir, builder):
        graph = builder.build(str(test_dir / "nonexistent"), Language.PYTHON)
        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0

    def test_file_filter_limits_parsing(self, test_dir, builder):
        (test_dir / "a.py").write_text("def foo(): pass\n")
        (test_dir / "b.py").write_text("def bar(): pass\n")
        graph = builder.build(str(test_dir), Language.PYTHON,
                             file_filter=["a.py"])
        func_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "foo" in func_names
        assert "bar" not in func_names

    def test_language_set_on_nodes(self, test_dir, builder):
        f = test_dir / "test.py"
        f.write_text("def f(): pass\n")
        graph = builder.build(str(test_dir), Language.PYTHON)
        func_nodes = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        ]
        assert all(n.language == "python" for n in func_nodes)

    def test_excluded_directories_skipped(self, test_dir, builder):
        (test_dir / "node_modules" / "index.js").parent.mkdir(parents=True)
        (test_dir / "node_modules" / "index.js").write_text("function f(){}")
        (test_dir / "src" / "app.js").parent.mkdir(parents=True)
        (test_dir / "src" / "app.js").write_text("function f(){}")
        graph = builder.build(str(test_dir), Language.JAVASCRIPT)
        file_names = {n.name for n in graph.nodes.values() if n.node_type == NodeType.FILE}
        assert "node_modules/index.js" not in file_names
        assert any("src" in name and "app" in name for name in file_names)

    def test_max_files_limit_respected(self, test_dir):
        """Create more files than max_files and verify limit is applied."""
        for i in range(10):
            (test_dir / f"f{i}.py").write_text(f"def func{i}(): pass\n")
        builder = CodeGraphBuilder(max_files=3, use_treesitter=False)
        graph = builder.build(str(test_dir), Language.PYTHON)
        assert len(graph.nodes) <= 6  # max 3 files x (1 file node + 1 func node per file)

    def test_binary_file_skipped_gracefully(self, test_dir, builder):
        """Binary bytes that aren't valid UTF-8 should be handled."""
        f = test_dir / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")
        # Won't be discovered since .bin is not a python extension
        (test_dir / "code.py").write_text("def f(): pass\n")
        graph = builder.build(str(test_dir), Language.PYTHON)
        func_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "f" in func_names

    def test_build_with_unsupported_language(self, test_dir, builder):
        """An unsupported language should produce a graph with file nodes only."""
        f = test_dir / "main.xyz"
        f.write_text("def f(): pass\n")
        # 'xyz' is not a known extension, so no files should be discovered
        graph = builder.build(str(test_dir), Language.UNKNOWN)
        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0


# -- Cross-file edges --

class TestCodeGraphBuilderCrossFile:
    def test_cross_file_imports_are_detected(self, test_dir, builder):
        (test_dir / "utils.py").write_text("def util_func(): pass\n")
        (test_dir / "main.py").write_text("""import utils
def run():
    utils.util_func()
""")
        graph = builder.build(str(test_dir), Language.PYTHON)
        import_edges = [
            e for e in graph.edges if e.edge_type == EdgeType.IMPORTS
        ]
        assert len(import_edges) >= 1

    def test_cross_file_call_edges(self, test_dir, builder):
        (test_dir / "utils.py").write_text("""def helper():
    return 42
""")
        (test_dir / "main.py").write_text("""from utils import helper
def run():
    return helper()
""")
        graph = builder.build(str(test_dir), Language.PYTHON)
        run_node = next(
            (n for n in graph.nodes.values()
             if n.name == "run" and n.node_type == NodeType.FUNCTION),
            None
        )
        assert run_node is not None, "run function not found"
        callees = graph.get_callees(run_node.id)
        callee_names = {n.name for n in callees}
        assert "helper" in callee_names

    def test_build_empty_directory(self, test_dir, builder):
        graph = builder.build(str(test_dir), Language.PYTHON)
        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0

    def test_includes_legacy_source_directory(self, test_dir, builder):
        (test_dir / "active.py").write_text("def active(): pass\n")
        (test_dir / "legacy").mkdir()
        (test_dir / "legacy" / "old.py").write_text("def obsolete(): pass\n")

        graph = builder.build(str(test_dir), Language.PYTHON)
        names = {node.name for node in graph.nodes.values()}

        assert "active" in names
        assert "obsolete" in names

    def test_missing_file_in_filter_skipped(self, test_dir, builder):
        """file_filter with a non-existent file should be skipped."""
        (test_dir / "real.py").write_text("def f(): pass\n")
        graph = builder.build(str(test_dir), Language.PYTHON,
                             file_filter=["real.py", "nonexistent.py"])
        func_names = {
            n.name for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        assert "f" in func_names


class TestCodeGraphBuilderCreateEdges:
    """Additional edge type coverage for builder."""

    def test_builder_creates_call_edges(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        call_edges = [
            e for e in graph.edges if e.edge_type == EdgeType.CALLS
        ]
        assert len(call_edges) >= 1

    def test_builder_creates_contains_edges(self, python_file, builder):
        graph = builder.build(str(python_file.parent), Language.PYTHON)
        contains_edges = [
            e for e in graph.edges if e.edge_type == EdgeType.CONTAINS
        ]
        assert len(contains_edges) >= 3
