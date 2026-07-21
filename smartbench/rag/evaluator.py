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
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path


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

        for item in data:
            self.queries.append(EvalQuery(
                query=item["query"],
                expected_file=item["expected_file"],
                expected_line=item.get("expected_line", 0),
                description=item.get("description", ""),
            ))

    def evaluate(self, k_values: Tuple[int, ...] = (1, 3, 5, 10)) -> EvalReport:
        """Run evaluation on all loaded queries.

        Args:
            k_values: Values of k for Hit@k calculation.

        Returns:
            EvalReport with all metrics.
        """
        report = EvalReport(total_queries=len(self.queries))

        # Detect retrieval mode
        from smartbench.rag.retriever import HybridRetriever
        if isinstance(self.retriever, HybridRetriever):
            if self.retriever.vector_store:
                report.retrieval_mode = "hybrid"
            else:
                report.retrieval_mode = "graph_only"
        else:
            report.retrieval_mode = "graph_only"

        hits_at_k: Dict[int, int] = {k: 0 for k in k_values}
        reciprocal_ranks: List[float] = []
        latencies: List[float] = []

        for eq in self.queries:
            start = time.time()
            context = self.retriever.retrieve(eq.query)
            elapsed = (time.time() - start) * 1000
            latencies.append(elapsed)

            # Extract file paths from retrieved context
            retrieved_files = self._extract_files_from_context(context)

            # Find rank of expected file
            rank = self._find_rank(eq.expected_file, retrieved_files)

            # Update hits
            for k in k_values:
                if rank is not None and rank <= k:
                    hits_at_k[k] += 1

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
            k: hits_at_k[k] / total for k in k_values
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
            if '.' in f and '/' in f:
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

        Uses fuzzy matching: checks if expected filename appears anywhere
        in the retrieved path.
        """
        expected_name = Path(expected).name
        expected_stem = Path(expected).stem

        for i, f in enumerate(retrieved):
            f_name = Path(f).name
            # Exact match
            if f == expected or f.endswith(expected):
                return i + 1
            # Filename match
            if f_name == expected_name:
                return i + 1
            # Stem match (handles .py vs .pyi etc.)
            if Path(f).stem == expected_stem:
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
    root = Path(project_path)
    py_files = sorted(root.rglob("*.py"))[:30]
    py_files = [
        str(f.relative_to(root))
        for f in py_files
        if "test_" not in f.name and "__pycache__" not in str(f)
    ]

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
