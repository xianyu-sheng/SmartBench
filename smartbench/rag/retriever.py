"""
HybridRetriever — combine graph structural retrieval with vector semantic search.

Merges results from:
  1. GraphRetriever (call chains, file containment, structural context)
  2. VectorStore (semantic similarity search)

Key feature: cross-validates structural claims against actual source code.
"""

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

from smartbench.graph.retriever import GraphRetriever
from smartbench.graph.schema import CodeGraph
from smartbench.path_safety import is_project_file, resolve_project_file
from smartbench.rag.embedder import CodeEmbedder
from smartbench.rag.store import VectorStore

logger = logging.getLogger(__name__)


class HybridRetriever:
    """
    Combines graph-based and vector-based code retrieval.

    Merge strategy:
      1. Query graph for structural context (call chains, containment)
      2. Query vector store for semantic similarity
      3. Deduplicate by file path
      4. Allocate the output budget between sources using the configured weights
      5. Format for LLM prompt injection

    Usage:
        hybrid = HybridRetriever(graph, project_path, vector_store, embedder)
        context = hybrid.retrieve("error handling in async code")
    """

    def __init__(self, graph: CodeGraph, project_path: str,
                 vector_store: Optional[VectorStore] = None,
                 embedder: Optional[CodeEmbedder] = None,
                 graph_weight: float = 0.4,
                 vector_weight: float = 0.6,
                 min_score: float = 0.15,
                 max_context_chars: int = 12000):
        """
        Args:
            graph: The code graph for structural retrieval
            project_path: Root directory of the project
            vector_store: Optional ChromaDB vector store
            embedder: Optional embedder for query vectorization
            graph_weight: Relative context budget for graph evidence
            vector_weight: Relative context budget for vector evidence
            min_score: Minimum similarity score to include a result (0.0-1.0)
        """
        self.graph = graph
        self.project_path = project_path
        self.vector_store = vector_store
        self.embedder = embedder
        self.graph_weight, self.vector_weight = self._normalize_weights(
            graph_weight, vector_weight
        )
        try:
            self.max_chars = max(1, int(max_context_chars))
        except (TypeError, ValueError):
            self.max_chars = 12000
        self.graph_retriever = GraphRetriever(
            graph,
            project_path,
            max_tokens_estimate=max(1, self.max_chars // 3),
        )
        try:
            self.min_score = max(0.0, min(float(min_score), 1.0))
        except (TypeError, ValueError):
            self.min_score = 0.15
        self.last_retrieved_files: List[str] = []

    @staticmethod
    def _normalize_weights(
        graph_weight: float,
        vector_weight: float,
    ) -> tuple[float, float]:
        """Validate and normalize source weights to a total of one."""
        try:
            graph_value = float(graph_weight)
            vector_value = float(vector_weight)
        except (TypeError, ValueError):
            raise ValueError("retrieval weights must be finite numbers") from None
        if (
            not math.isfinite(graph_value)
            or not math.isfinite(vector_value)
            or graph_value < 0
            or vector_value < 0
        ):
            raise ValueError("retrieval weights must be finite and non-negative")
        total = graph_value + vector_value
        if total <= 0:
            raise ValueError("at least one retrieval weight must be positive")
        return graph_value / total, vector_value / total

    # ── Public API ──────────────────────────────────────────────────────

    def retrieve(self, query: str, n_results: int = 15) -> str:
        """
        Hybrid retrieval with re-ranking, dedup, and confidence filtering.

        Pipeline:
          1. Query graph for structural context
          2. Query vector store for semantic similarity
          3. Filter low-confidence results (score < min_score)
          4. Rank vector results by similarity score
          5. Deduplicate by file (keep highest-scoring chunk per file)
          6. Allocate context between graph and vector evidence by source weight

        Args:
            query: Natural language query (user concern or proposal text)
            n_results: Max results from each source

        Returns:
            Formatted string for LLM prompt injection
        """
        self.last_retrieved_files = []
        if not isinstance(query, str):
            query = str(query)
        try:
            n_results = max(1, int(n_results))
        except (TypeError, ValueError):
            n_results = 15

        # Get graph context unless graph evidence was explicitly disabled.
        graph_context = ""
        graph_files: List[str] = []
        if self.graph_weight > 0:
            graph_context = self.graph_retriever.retrieve(query)
            graph_files = list(self.graph_retriever.last_retrieved_files)
        graph_had_results = bool(graph_files)

        # Get vector results if available
        vector_blocks = []
        if self.vector_weight > 0 and self.vector_store and self.embedder:
            try:
                vector_results = self.vector_store.search_by_text(
                    query, self.embedder, n_results=n_results
                )
                # ── Filter low-confidence results ──
                vector_results = [
                    r for r in vector_results
                    if r.get("score", 0) >= self.min_score
                ]
                # ── Re-rank: sort by score descending ──
                vector_results.sort(
                    key=lambda r: r.get("score", 0), reverse=True
                )
                # ── Deduplicate by file: keep highest score per file ──
                vector_results = self._deduplicate_by_file(vector_results)

                vector_blocks = self._format_vector_results(vector_results)
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # A single available source receives the full output budget.
        if not vector_blocks:
            visible_context = self._truncate_context(
                graph_context or "/* No retrieval source available */",
                self.max_chars,
                "\n// ... (graph context truncated)",
            )
            self.last_retrieved_files = self._visible_files(
                visible_context, graph_files
            )
            return visible_context

        if not graph_had_results:
            vector_context = self._vector_context(vector_blocks)
            visible_context = self._truncate_context(
                vector_context,
                self.max_chars,
                "\n// ... (semantic context truncated)",
            )
            self.last_retrieved_files = self._visible_block_files(
                visible_context, vector_blocks
            )
            return visible_context

        # Deduplicate: skip vector results for files already covered by graph
        new_blocks = self._deduplicate_blocks(
            vector_blocks, graph_context, graph_files=graph_files
        )

        if not new_blocks:
            visible_context = self._truncate_context(
                graph_context,
                self.max_chars,
                "\n// ... (graph context truncated)",
            )
            self.last_retrieved_files = self._visible_files(
                visible_context, graph_files
            )
            return visible_context

        # Both sources are present: divide the bounded context between them.
        separator = "\n"
        available_chars = max(1, self.max_chars - len(separator))
        graph_budget = int(available_chars * self.graph_weight)
        vector_budget = available_chars - graph_budget
        graph_display = self._truncate_context(
            graph_context,
            graph_budget,
            "\n// ... (graph context truncated)",
        )
        vector_context = self._vector_context(new_blocks)
        vector_display = self._truncate_context(
            vector_context,
            vector_budget,
            "\n// ... (semantic context truncated)",
        )

        # A very small positive budget may not fit useful evidence. Give the
        # other source the full budget instead of returning a partial header.
        if not graph_display:
            return self._use_vector_context(vector_context, new_blocks)
        if not vector_display:
            visible_context = self._truncate_context(
                graph_context,
                self.max_chars,
                "\n// ... (graph context truncated)",
            )
            self.last_retrieved_files = self._visible_files(
                visible_context, graph_files
            )
            return visible_context

        combined = graph_display + separator + vector_display
        visible_files = self._visible_files(graph_display, graph_files)
        visible_vector_files = self._visible_block_files(
            vector_display, new_blocks
        )
        for file_path in visible_vector_files:
            if file_path not in visible_files:
                visible_files.append(file_path)
        self.last_retrieved_files = visible_files
        return combined

    def _use_vector_context(
        self,
        vector_context: str,
        vector_blocks: List[str],
    ) -> str:
        """Use the complete output budget for vector evidence."""
        visible_context = self._truncate_context(
            vector_context,
            self.max_chars,
            "\n// ... (semantic context truncated)",
        )
        self.last_retrieved_files = self._visible_block_files(
            visible_context, vector_blocks
        )
        return visible_context

    @staticmethod
    def _truncate_context(context: str, budget: int, marker: str) -> str:
        """Truncate text to an exact character budget."""
        if budget <= 0:
            return ""
        if len(context) <= budget:
            return context
        if budget <= len(marker):
            return context[:budget]
        return context[:budget - len(marker)] + marker

    @staticmethod
    def _visible_files(context: str, candidates: List[str]) -> List[str]:
        """Return graph files whose complete generated header is visible."""
        visible = []
        for file_path in candidates:
            header = f"// ── {file_path} ──"
            position = context.find(header)
            if position >= 0 and position + len(header) <= len(context):
                visible.append(file_path)
        return visible

    @classmethod
    def _visible_block_files(
        cls,
        context: str,
        blocks: List[str],
    ) -> List[str]:
        """Return vector files whose complete generated header is visible."""
        visible = []
        for block in blocks:
            first_line = block.split("\n", 1)[0]
            file_path = cls._block_file_path(block)
            position = context.find(first_line)
            if (
                file_path
                and position >= 0
                and position + len(first_line) <= len(context)
                and file_path not in visible
            ):
                visible.append(file_path)
        return visible

    @staticmethod
    def _vector_context(blocks: List[str]) -> str:
        """Format vector blocks with a source label."""
        return "\n".join([
            "// ── Semantic Search Results (RAG) ──",
            *blocks,
        ])

    def _deduplicate_by_file(self, results: List[Dict]) -> List[Dict]:
        """Deduplicate vector results: keep only the highest-scoring chunk per file."""
        seen_files: Dict[str, Dict] = {}
        for r in results:
            meta = r.get("metadata", {})
            file_path = meta.get("file_path", "")
            if not file_path:
                continue
            if file_path not in seen_files or r.get("score", 0) > seen_files[file_path].get("score", 0):
                seen_files[file_path] = r
        return list(seen_files.values())

    def verify_location(self, file_path: str,
                        line: Optional[int] = None) -> Dict:
        """
        Verify that a claimed file:line reference exists.

        Returns:
            {
                "exists": bool,
                "resolved_path": str or None,
                "actual_line_exists": bool,
                "content_at_line": str or None,
            }
        """
        result = {
            "exists": False,
            "resolved_path": None,
            "actual_line_exists": False,
            "content_at_line": None,
        }

        # Try exact match
        root = Path(self.project_path).resolve()
        full_path = resolve_project_file(root, file_path)
        if full_path is not None:
            result["exists"] = True
            result["resolved_path"] = str(full_path.relative_to(root)).replace(
                "\\", "/"
            )
        else:
            # Fuzzy search
            resolved = self._fuzzy_resolve_path(file_path)
            if resolved:
                result["exists"] = True
                result["resolved_path"] = resolved
                full_path = resolve_project_file(root, resolved)

        if result["exists"] and line is not None and full_path is not None:
            try:
                lines = full_path.read_text(
                    encoding='utf-8', errors='ignore'
                ).split('\n')
                if 1 <= line <= len(lines):
                    result["actual_line_exists"] = True
                    # Extract context around the line
                    ctx_start = max(0, line - 3)
                    ctx_end = min(len(lines), line + 2)
                    result["content_at_line"] = '\n'.join(lines[ctx_start:ctx_end])
            except Exception:
                pass

        return result

    def retrieve_claim_evidence(self,
                                claim: Dict) -> Optional[Dict]:
        """
        For a debate proposal claim, retrieve supporting or refuting evidence.

        Args:
            claim: Dict with keys like "file_path", "line", "function", "context"

        Returns:
            Dict with verification data or None
        """
        file_path = claim.get("file_path", "") or claim.get("location", "")
        line = claim.get("line") or claim.get("line_start")

        # Parse "file:line" format
        if ":" in file_path and not line:
            parts = file_path.rsplit(":", 1)
            file_path = parts[0]
            try:
                line = int(parts[1])
            except ValueError:
                pass

        verif = self.verify_location(file_path, line)

        # If file not found, try semantic search for the claim's context
        if not verif["exists"] and self.vector_store and self.embedder:
            context = claim.get("context", "") or claim.get("description", "")
            if context:
                similar = self.vector_store.search_by_text(
                    context, self.embedder, n_results=3
                )
                if similar:
                    verif["semantic_matches"] = [
                        {
                            "file": s["metadata"].get("file_path", ""),
                            "line": s["metadata"].get("start_line", 0),
                            "score": s["score"],
                            "content": s["content"][:300],
                        }
                        for s in similar[:3]
                    ]

        return verif

    # ── Internal formatting ─────────────────────────────────────────────

    def _format_vector_results(self, results: List[Dict]) -> List[str]:
        """Format vector search results as code blocks."""
        blocks = []
        for r in results:
            meta = r.get("metadata", {})
            file_path = meta.get("file_path", "?")
            start = meta.get("start_line", 0)
            end = meta.get("end_line", 0)
            node_type = meta.get("node_type", "")
            node_name = meta.get("node_name", "")
            score = r.get("score", 0)

            line_info = f"line {start}" if not end else f"line {start}-{end}"
            header = (
                f"// [{node_type}] {node_name} ({line_info}) "
                f"[sim: {score:.2f}]"
            )

            content = r.get("content", "")
            # Remove internal header line if present (starts with "# file:")
            content_lines = content.split('\n')
            if content_lines and content_lines[0].startswith('# file:'):
                content_lines = content_lines[1:]
            body = '\n'.join(content_lines)[:800]

            blocks.append(f"// ── {file_path} ──\n{header}\n{body}")

        return blocks

    def _deduplicate_blocks(
        self,
        vector_blocks: List[str],
        graph_context: str,
        graph_files: Optional[List[str]] = None,
    ) -> List[str]:
        """Remove vector blocks for files already covered by graph context."""
        exact_graph_files = set(graph_files or [])
        if graph_files is None:
            for line in graph_context.split('\n'):
                path = self._block_file_path(line)
                if path:
                    exact_graph_files.add(path)

        new_blocks = []
        for block in vector_blocks:
            if self._block_file_path(block) not in exact_graph_files:
                new_blocks.append(block)

        return new_blocks

    @staticmethod
    def _block_file_path(block: str) -> str:
        """Read the generated path header without inspecting source content."""
        first_line = block.split('\n', 1)[0]
        prefix = "// ── "
        suffix = " ──"
        if first_line.startswith(prefix) and first_line.endswith(suffix):
            return first_line[len(prefix):-len(suffix)]
        return ""

    def _fuzzy_resolve_path(self, claimed: str) -> Optional[str]:
        """
        Fuzzy path resolution for LLM-hallucinated paths.

        Strategies:
          1. Search by filename only
          2. Search by path suffix
          3. Levenshtein distance on stem
        """
        claimed_name = Path(claimed).name
        claimed_suffix = Path(claimed).suffix

        candidates = []
        root = Path(self.project_path).resolve()

        for f in root.rglob('*'):
            if not is_project_file(root, f):
                continue

            try:
                rel = str(f.relative_to(root)).replace('\\', '/')
            except ValueError:
                continue

            score = 0.0

            # Exact path match
            if rel == claimed:
                return rel

            # Exact filename match (highest reward)
            if f.name == claimed_name:
                score += 0.7
            elif f.name.lower() == claimed_name.lower():
                score += 0.6

            # Suffix match
            if claimed_suffix and f.suffix == claimed_suffix:
                score += 0.1
            elif claimed_suffix and f.suffix.lower() == claimed_suffix.lower():
                score += 0.05

            # Path segment overlap
            claimed_parts = set(Path(claimed).parts)
            rel_parts = set(Path(rel).parts)
            overlap = len(claimed_parts & rel_parts)
            if overlap > 0:
                score += overlap * 0.1

            if score > 0.3:
                candidates.append((rel, score))

        if candidates:
            candidates.sort(key=lambda x: -x[1])
            return candidates[0][0]

        return None
