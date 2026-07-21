"""
GraphRetriever — semantic search and context extraction from CodeGraph.

Given a signal (function name, error message, keyword), find relevant
code nodes and format their context for LLM prompt injection.

This replaces the old "read entire key files" approach with
targeted graph-based context retrieval.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from smartbench.graph.schema import CodeGraph, CodeNode, EdgeType, NodeType


class GraphRetriever:
    """
    Retrieves relevant code context from a CodeGraph.

    Strategy:
    1. Find seed nodes matching the query (name search, keyword, etc.)
    2. Expand N hops around seeds to capture callers, callees, and related code
    3. Read actual source code for the retrieved nodes
    4. Format as structured context for LLM prompt injection

    This is much more token-efficient than full-file reading:
    instead of 3000 lines of irrelevant code, the LLM gets
    exactly the functions and their neighbors that matter.
    """

    def __init__(self, graph: CodeGraph, project_path: str,
                 max_tokens_estimate: int = 4000):
        """
        Args:
            graph: The built CodeGraph
            project_path: Root path for reading source files
            max_tokens_estimate: Rough token budget (chars ≈ tokens for code)
        """
        self.graph = graph
        self.project_path = project_path
        self.max_chars = max_tokens_estimate * 3  # rough: 1 token ≈ 3 chars for code

    def retrieve(self, query: str, hops: int = 2,
                 max_nodes: int = 15) -> str:
        """
        Main entry point: given a query string, return formatted code context.

        Args:
            query: A function name, error keyword, file path, or natural language
            hops: How many graph hops to expand from seed nodes
            max_nodes: Maximum nodes to include in the context

        Returns:
            Formatted string ready for LLM prompt injection
        """
        # 1. Find seed nodes
        seeds = self._find_seeds(query)

        if not seeds:
            return f"/* No relevant code found for query: '{query}' */"

        # 2. Expand subgraph
        seed_ids = [n.id for n in seeds]
        subgraph = self.graph.expand(
            seed_ids,
            hops=hops,
            edge_types={EdgeType.CALLS, EdgeType.CONTAINS},
            direction="both",
        )

        # 3. Rank and select nodes (prioritize functions, then classes, then files)
        ranked = self._rank_nodes(subgraph, seeds, query)

        # 4. Read source code for selected nodes
        context = self._format_context(ranked[:max_nodes])

        return context

    def retrieve_by_hotspot(self, function_names: List[str],
                             hops: int = 2) -> str:
        """
        Retrieve context around specific function names (from profiling/perf data).
        """
        seeds = []
        for name in function_names:
            seeds.extend(self.graph.find_by_name(name, NodeType.FUNCTION))

        if not seeds:
            return "/* No functions found for hotspots */"

        seed_ids = [n.id for n in seeds]
        subgraph = self.graph.expand(seed_ids, hops=hops, direction="both")
        ranked = self._rank_nodes(subgraph, seeds, function_names[0])
        return self._format_context(ranked[:15])

    def retrieve_by_file(self, file_path: str, focus_lines: Optional[Tuple[int, int]] = None) -> str:
        """
        Retrieve context from a specific file, optionally focused on a line range.
        """
        import os
        full_path = os.path.join(self.project_path, file_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except (OSError, FileNotFoundError):
            return f"/* Could not read file: {file_path} */"

        if focus_lines:
            start, end = focus_lines
            # Add context padding
            pad_start = max(0, start - 10)
            pad_end = min(len(lines), end + 10)
            selected = lines[pad_start:pad_end]
            return f"// {file_path}:{pad_start+1}-{pad_end}\n" + "".join(selected)

        return f"// {file_path}:1-{len(lines)}\n" + "".join(lines)

    # ── Seed finding ──────────────────────────────────────────────────

    _QUERY_STOPWORDS = {
        "a", "an", "and", "are", "code", "does", "file", "files",
        "find", "for", "from", "how", "implementation", "in", "is",
        "it", "of", "the", "to", "where", "with", "work", "works",
        "defined", "generated", "based", "source",
    }

    def _find_seeds(self, query: str) -> List[CodeNode]:
        """
        Multi-strategy seed finding for both identifiers and natural language.
        """
        seeds: List[CodeNode] = []
        query_lower = query.lower().strip()

        # Strategy 1: Exact name match on functions/classes
        for node in self.graph.nodes.values():
            if node.node_type in (NodeType.FUNCTION, NodeType.CLASS):
                if node.name.lower() == query_lower:
                    seeds.append(node)

        if seeds:
            return seeds

        # Strategy 2: Natural-language token scoring across names and paths.
        query_tokens = self._identifier_tokens(query_lower) - self._QUERY_STOPWORDS
        candidates = []
        for node in self.graph.nodes.values():
            if node.node_type not in (NodeType.FUNCTION, NodeType.CLASS, NodeType.FILE):
                continue
            name_tokens = self._identifier_tokens(node.name)
            path_tokens = self._identifier_tokens(node.file_path)
            signature_tokens = self._identifier_tokens(
                node.properties.get("signature", "")
            )
            score = 0.0
            for token in query_tokens:
                score += 4.0 * self._best_token_match(token, name_tokens)
                score += 2.5 * self._best_token_match(token, path_tokens)
                score += self._best_token_match(token, signature_tokens)
            if node.node_type == NodeType.FILE:
                file_stem = Path(node.file_path).stem.lower()
                if file_stem in query_tokens:
                    score += 3.0
            if score > 0:
                candidates.append((score, node))

        candidates.sort(key=lambda item: (-item[0], item[1].file_path, item[1].name))
        return [node for _, node in candidates[:10]]

    @staticmethod
    def _identifier_tokens(value: str) -> set[str]:
        """Split paths, snake_case, kebab-case, and CamelCase identifiers."""
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
        return {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", expanded)
            if len(token) > 1
        }

    @staticmethod
    def _best_token_match(query_token: str, candidates: set[str]) -> float:
        """Return a conservative lexical similarity in the range 0..1."""
        best = 0.0
        for candidate in candidates:
            if query_token == candidate:
                return 1.0
            shorter = min(len(query_token), len(candidate))
            if shorter >= 5 and (
                query_token.startswith(candidate[:5])
                or candidate.startswith(query_token[:5])
            ):
                best = max(best, 0.7)
            elif shorter >= 4 and (
                query_token in candidate or candidate in query_token
            ):
                best = max(best, 0.5)
        return best

    # ── Ranking ───────────────────────────────────────────────────────

    def _rank_nodes(self, subgraph: CodeGraph, seeds: List[CodeNode],
                    query: str) -> List[CodeNode]:
        """
        Rank nodes by relevance:
        - Seeds first
        - Direct callers/callees of seeds next
        - Higher-degree nodes (more connections) ranked higher
        - Name similarity to query boosts score
        - Files come last
        """
        seed_ids = {s.id for s in seeds}
        query_lower = query.lower().strip()

        def name_match_score(node: CodeNode) -> int:
            """Boost nodes whose name contains query terms."""
            terms = query_lower.split()
            node_name = node.name.lower()
            matches = sum(1 for t in terms if t in node_name)
            return matches

        def score(node: CodeNode) -> Tuple[int, int, int, int]:
            # Tier: 0=seed, 1=direct neighbor, 2=other
            if node.id in seed_ids:
                tier = 0
            elif any(
                e.source_id in seed_ids or e.target_id in seed_ids
                for e in subgraph.edges
                if e.source_id == node.id or e.target_id == node.id
            ):
                tier = 1
            else:
                tier = 2

            # Degree
            degree = sum(
                1 for e in subgraph.edges
                if e.source_id == node.id or e.target_id == node.id
            )

            # Type priority: FUNCTION > CLASS > FILE > other
            type_prio = {
                NodeType.FUNCTION: 0,
                NodeType.CLASS: 1,
                NodeType.FILE: 2,
            }.get(node.node_type, 3)

            # Negate name_match so higher match = better (lower score)
            return (tier, -degree, type_prio, -name_match_score(node))

        return sorted(subgraph.nodes.values(), key=score)

    # ── Formatting ────────────────────────────────────────────────────

    def _format_context(self, nodes: List[CodeNode]) -> str:
        """Read actual source code for nodes and format for LLM."""
        import os

        # Group by file for efficient reading
        by_file: dict = {}
        for node in nodes:
            by_file.setdefault(node.file_path, []).append(node)

        sections = []
        total_chars = 0

        for file_path, file_nodes in by_file.items():
            full_path = os.path.join(self.project_path, file_path)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
            except (OSError, FileNotFoundError):
                continue

            file_section = f"\n// ── {file_path} ──\n"
            file_chars = len(file_section)

            for node in file_nodes:
                if total_chars + file_chars > self.max_chars:
                    break

                start = max(0, node.line_start - 3)
                end = min(len(all_lines), node.line_end if node.line_end else start + 30)

                # Extract the code block
                code_block = "".join(all_lines[start:end])
                block_header = (
                    f"\n// [{node.node_type.value}] {node.name} "
                    f"(line {node.line_start})"
                )
                if node.properties.get("signature"):
                    block_header += f"\n// sig: {node.properties['signature']}"

                block_text = f"{block_header}\n{code_block}"
                file_chars += len(block_text)

                if total_chars + file_chars <= self.max_chars:
                    file_section += block_text

            sections.append(file_section)
            total_chars += file_chars

            if total_chars > self.max_chars:
                sections.append("\n// ... (context truncated, token budget reached)\n")
                break

        return "\n".join(sections) if sections else "/* No context available */"
