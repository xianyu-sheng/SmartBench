"""
RAG evaluation framework — measures retrieval quality.

Metrics:
  - Hit Rate @k: fraction of queries where the correct file is in top-k results
  - MRR (Mean Reciprocal Rank): average of 1/rank for the first correct result
  - Precision @k: fraction of top-k results that are relevant

Usage:
    evaluator = RAGEvaluator(hybrid_retriever)
    evaluator.load_queries("eval_queries.json")
    report = evaluator.evaluate(k_values=[1, 3, 5, 10])
    print(report.summary())
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from smartbench.path_safety import is_project_file

_EVAL_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".smartbench",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
_EVAL_MAX_DIRECTORIES = 2_000
_EVAL_MAX_DISCOVERED_FILES = 20_000
_EVAL_MAX_PYTHON_FILES = 30


@dataclass
class EvalQuery:
    """A single evaluation query with known ground truth."""
    query: str
    expected_file: str         # relative path that should be retrieved
    expected_line: int = 0     # optional: specific line
    description: str = ""      # human-readable description


@dataclass
class EvalReport:
    """Evaluation results for a set of queries."""
    total_queries: int = 0
    hit_rates: Dict[int, float] = field(default_factory=dict)   # k → rate
    precision_at_k: Dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    avg_latency_ms: float = 0.0
    per_query: List[Dict] = field(default_factory=list)
    retrieval_mode: str = "unknown"  # "graph_only", "rag_only", "hybrid"

    def summary(self) -> str:
        lines = [
            f"RAG Evaluation Report ({self.retrieval_mode})",
            f"  Queries: {self.total_queries}",
            f"  MRR:     {self.mrr:.3f}",
        ]
        for k, rate in sorted(self.hit_rates.items()):
            lines.append(f"  Hit@{k}:   {rate:.1%}")
        for k, precision in sorted(self.precision_at_k.items()):
            lines.append(f"  P@{k}:     {precision:.3f}")
        lines.append(f"  Avg latency: {self.avg_latency_ms:.1f}ms")
        return "\n".join(lines)


class RAGEvaluator:
    """Measures code retrieval quality for a given retriever."""

    def __init__(self, retriever, project_path: str):
        """
        Args:
            retriever: HybridRetriever or GraphRetriever instance.
            project_path: Root of the project being evaluated.
        """
        self.retriever = retriever
        self.project_path = Path(project_path)
        self.queries: List[EvalQuery] = []

    def load_queries(self, queries_path: str) -> None:
        """Load evaluation queries from a JSON file."""
        with open(queries_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("Evaluation query file must contain a JSON list")
        self.queries.clear()
        for item in data:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("query"), str)
                or not item["query"].strip()
                or not isinstance(item.get("expected_file"), str)
                or not item["expected_file"].strip()
            ):
                raise ValueError("Each evaluation item needs query and expected_file")
            expected_line = item.get("expected_line", 0)
            if (
                not isinstance(expected_line, int)
                or isinstance(expected_line, bool)
                or expected_line < 0
            ):
                raise ValueError("expected_line must be a non-negative integer")
            self.queries.append(EvalQuery(
                query=item["query"],
                expected_file=item["expected_file"],
                expected_line=expected_line,
                description=(
                    item.get("description", "")
                    if isinstance(item.get("description", ""), str)
                    else ""
                ),
            ))

    def evaluate(self, k_values: Tuple[int, ...] = (1, 3, 5, 10)) -> EvalReport:
        """Run evaluation on all loaded queries.

        Args:
            k_values: Values of k for Hit@k calculation.

        Returns:
            EvalReport with all metrics.
        """
        if self.retriever is None or not hasattr(self.retriever, "retrieve"):
            raise ValueError("A retriever with a retrieve(query) method is required")
        if not self.queries:
            raise ValueError("No evaluation queries loaded")
        try:
            normalized_k = tuple(sorted({int(k) for k in k_values if int(k) > 0}))
        except (TypeError, ValueError):
            raise ValueError("k_values must contain positive integers") from None
        if not normalized_k:
            raise ValueError("k_values must contain at least one positive integer")
        report = EvalReport(total_queries=len(self.queries))

        # Detect retrieval mode
        from smartbench.rag.retriever import HybridRetriever
        if isinstance(self.retriever, HybridRetriever):
            if self.retriever.vector_store and self.retriever.embedder:
                report.retrieval_mode = "hybrid"
            else:
                report.retrieval_mode = "graph_only"
        else:
            report.retrieval_mode = "graph_only"

        hits_at_k: Dict[int, int] = {k: 0 for k in normalized_k}
        precision_sums: Dict[int, float] = {k: 0.0 for k in normalized_k}
        reciprocal_ranks: List[float] = []
        latencies: List[float] = []

        for eq in self.queries:
            start = time.perf_counter()
            context = self.retriever.retrieve(eq.query)
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)

            # Extract file paths from retrieved context
            structured_files = getattr(
                self.retriever, "last_retrieved_files", None
            )
            retrieved_files = (
                list(structured_files)
                if isinstance(structured_files, list)
                else self._extract_files_from_context(context)
            )

            # Find rank of expected file
            rank = self._find_rank(eq.expected_file, retrieved_files)

            # Update hits
            for k in normalized_k:
                if rank is not None and rank <= k:
                    hits_at_k[k] += 1
                    precision_sums[k] += 1.0 / k

            # MRR
            if rank is not None:
                reciprocal_ranks.append(1.0 / rank)
            else:
                reciprocal_ranks.append(0.0)

            report.per_query.append({
                "query": eq.query,
                "expected": eq.expected_file,
                "retrieved_files": retrieved_files[:5],
                "rank": rank,
                "latency_ms": round(elapsed, 1),
            })

        # Compute aggregate metrics
        total = max(len(self.queries), 1)
        report.hit_rates = {
            k: hits_at_k[k] / total for k in normalized_k
        }
        report.precision_at_k = {
            k: precision_sums[k] / total for k in normalized_k
        }
        report.mrr = sum(reciprocal_ranks) / total
        report.avg_latency_ms = sum(latencies) / total

        return report

    @staticmethod
    def _extract_files_from_context(context: str) -> List[str]:
        """Extract file paths from retrieved context text.

        Looks for patterns like:
          - "// ── path/to/file.py ──"
          - "file: path/to/file.go:42"
          - "file_path: src/main.rs"
        """
        import re
        files = []

        # Pattern: // ── path/to/file ──
        for m in re.finditer(r'──\s*(.+?)\s*──', context):
            f = m.group(1).strip()
            if Path(f).suffix:
                files.append(f)

        # Pattern: file_path or file: followed by a path
        for m in re.finditer(r'(?:file(?:_path)?:\s*)([\w./-]+\.\w{1,6})', context):
            files.append(m.group(1))

        # Deduplicate preserving order
        seen = set()
        result = []
        for f in files:
            if f not in seen:
                seen.add(f)
                result.append(f)

        return result

    @staticmethod
    def _find_rank(expected: str, retrieved: List[str]) -> Optional[int]:
        """Find the 1-based rank of the expected file in retrieved list.

        Directory-qualified ground truth requires an exact normalized suffix;
        this avoids inflating metrics when different folders share a filename.
        """
        expected_normalized = expected.replace('\\', '/').lstrip('./')
        expected_has_parent = '/' in expected_normalized

        for i, f in enumerate(retrieved):
            normalized = f.replace('\\', '/').lstrip('./')
            if normalized == expected_normalized:
                return i + 1
            if expected_has_parent and normalized.endswith('/' + expected_normalized):
                return i + 1
            if not expected_has_parent and Path(normalized).name == expected_normalized:
                return i + 1

        return None


def create_eval_queries(project_path: str, output_path: str) -> None:
    """Generate a template evaluation queries file for a project.

    Scans the project for key files and generates query templates
    that a human can fill in with expected answers.

    Args:
        project_path: Root of the project to generate queries for.
        output_path: Where to write the JSON template.
    """
    try:
        root = Path(project_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(f"Project path does not exist: {project_path}") from None
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {project_path}")

    py_files = _discover_eval_python_files(root)

    queries = []
    for f in py_files[:15]:
        stem = Path(f).stem.replace("_", " ")
        queries.append({
            "query": f"How does {stem} work?",
            "expected_file": f,
            "expected_line": 0,
            "description": f"Find the implementation of {stem}",
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(queries, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(queries)} template queries → {output_path}")
    print("Edit the 'expected_file' field with the correct answer for each query.")


def _discover_eval_python_files(root: Path) -> List[str]:
    """Discover a bounded set of project-owned Python source files."""
    files: List[str] = []
    visited_directories = 0
    discovered_files = 0

    for current, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        visited_directories += 1
        if visited_directories > _EVAL_MAX_DIRECTORIES:
            break

        current_path = Path(current)
        safe_dirs = []
        for dirname in sorted(dirnames):
            candidate = current_path / dirname
            if dirname in _EVAL_EXCLUDED_DIRS or candidate.is_symlink():
                continue
            try:
                candidate.resolve().relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            safe_dirs.append(dirname)
        dirnames[:] = safe_dirs

        for filename in sorted(filenames):
            discovered_files += 1
            if discovered_files > _EVAL_MAX_DISCOVERED_FILES:
                return files
            if not filename.endswith(".py"):
                continue
            if filename.startswith("test_") or filename.endswith("_test.py"):
                continue

            candidate = current_path / filename
            if not is_project_file(root, candidate):
                continue
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            files.append(relative)
            if len(files) >= _EVAL_MAX_PYTHON_FILES:
                return files

    return files
