"""
EvidenceExtractor — read actual source code at claimed locations.

Provides concrete code snippets for verification and prompt injection.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from smartbench.graph.schema import CodeGraph, NodeType

logger = logging.getLogger(__name__)


class EvidenceExtractor:
    """
    Extracts actual source code from disk at claimed locations.

    Used by the CrossChecker and Verifier to obtain ground-truth code
    for comparison against LLM-generated claims.
    """

    def __init__(self, project_path: str,
                 graph: Optional[CodeGraph] = None):
        """
        Args:
            project_path: Root directory of project
            graph: Optional CodeGraph for call chain extraction
        """
        self.project_path = Path(project_path)
        self.graph = graph
        self._file_cache: Dict[str, List[str]] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def extract_at(self, file_path: str,
                   line: Optional[int] = None,
                   context_lines: int = 8) -> Optional[str]:
        """
        Read code at file:line with surrounding context.

        Args:
            file_path: Relative file path
            line: 1-based line number (None = whole file)
            context_lines: Lines of context before/after the target line

        Returns:
            Code string or None if file missing
        """
        full_path = self.project_path / file_path
        if not full_path.exists():
            return None

        lines = self._read_file(full_path)

        if line is None:
            return '\n'.join(lines)

        if line < 1 or line > len(lines):
            return None

        start = max(0, line - 1 - context_lines)
        end = min(len(lines), line + context_lines)
        return '\n'.join(lines[start:end])

    def extract_function_at(self, file_path: str,
                            line: int) -> Optional[str]:
        """
        Find and extract the enclosing function/class at file:line.

        Uses graph nodes if available, otherwise heuristic scanning.

        Returns:
            Full function body or None
        """
        # Try graph first
        if self.graph:
            for node in self.graph.nodes.values():
                if (node.file_path == file_path and
                    node.node_type in (NodeType.FUNCTION, NodeType.CLASS) and
                    node.line_start <= line <= (node.line_end or (node.line_start + 100))):
                    return self.extract_at(file_path, node.line_start,
                                           context_lines=max(50, node.line_end - node.line_start))

        # Fallback: read a large window around the line
        content = self.extract_at(file_path, line, context_lines=60)
        return content

    def extract_call_chain(self, function_name: str,
                           file_path: Optional[str] = None) -> List[Dict]:
        """
        For a claimed function, return its actual callers and callees
        from the code graph.

        Args:
            function_name: Name of the function
            file_path: Optional file to scope the search

        Returns:
            List of {direction: "caller"/"callee", name, file, line}
        """
        if not self.graph:
            return []

        results = []

        # Find the function node
        target_nodes = []
        for node in self.graph.nodes.values():
            if node.node_type != NodeType.FUNCTION:
                continue
            if node.name.lower() == function_name.lower():
                if file_path and node.file_path != file_path:
                    continue
                target_nodes.append(node)

        for node in target_nodes:
            # Get callers (who calls this function)
            callers = self.graph.get_callers(node.id)
            for c in callers:
                results.append({
                    "direction": "caller",
                    "name": c.name,
                    "file": c.file_path,
                    "line": c.line_start,
                })

            # Get callees (what this function calls)
            callees = self.graph.get_callees(node.id)
            for c in callees:
                results.append({
                    "direction": "callee",
                    "name": c.name,
                    "file": c.file_path,
                    "line": c.line_start,
                })

        return results

    def verify_call_chain(self, claimed_chain: List[str]) -> Dict:
        """
        Verify a claimed call chain (e.g., ["handleRequest", "processData", "saveResult"])
        against the actual code graph.

        Returns:
            {
                "valid_edges": [("handleRequest", "processData"), ...],
                "missing_edges": [("processData", "saveResult"), ...],
                "accuracy": 0.67  # 2/3 valid
            }
        """
        result = {
            "valid_edges": [],
            "missing_edges": [],
            "accuracy": 0.0,
        }

        if not self.graph or len(claimed_chain) < 2:
            return result

        edges = list(zip(claimed_chain, claimed_chain[1:]))
        total = len(edges)
        valid = 0

        for caller_name, callee_name in edges:
            # Find caller node
            caller_nodes = [
                n for n in self.graph.nodes.values()
                if n.node_type == NodeType.FUNCTION and n.name.lower() == caller_name.lower()
            ]
            if not caller_nodes:
                result["missing_edges"].append((caller_name, callee_name))
                continue

            # Check if any caller → callee edge exists
            edge_found = False
            for cn in caller_nodes:
                callees = self.graph.get_callees(cn.id)
                if any(c.name.lower() == callee_name.lower() for c in callees):
                    edge_found = True
                    result["valid_edges"].append((caller_name, callee_name))
                    valid += 1
                    break

            if not edge_found:
                result["missing_edges"].append((caller_name, callee_name))

        result["accuracy"] = valid / total if total > 0 else 0.0
        return result

    # ── Internals ───────────────────────────────────────────────────────

    def _read_file(self, path: Path) -> List[str]:
        """Read file with caching."""
        key = str(path)
        if key not in self._file_cache:
            try:
                content = path.read_text(encoding='utf-8', errors='ignore')
                self._file_cache[key] = content.split('\n')
            except Exception:
                self._file_cache[key] = []
        return self._file_cache[key]
