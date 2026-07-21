"""
RAG module — vector-based semantic code retrieval for SmartBench.

Layers:
  1. CodeChunker — language-aware code splitting
  2. CodeEmbedder — local sentence-transformers embedding
  3. VectorStore — ChromaDB persistence
  4. IndexPipeline — orchestrate chunk→embed→store
  5. HybridRetriever — merge graph + vector results

All modules are optional: SmartBench core runs without them.
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Chunk:
    """A semantic code chunk ready for embedding."""
    id: str                              # deterministic hash
    content: str                         # the chunk text (with header)
    file_path: str                       # relative path
    start_line: int
    end_line: int
    language: str
    node_type: str                       # "function" / "class" / "file" / "block"
    node_name: str                       # function/class name or filename
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_id(file_path: str, start_line: int, end_line: int) -> str:
        import hashlib
        raw = f"{file_path}:{start_line}:{end_line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# Lazy imports to avoid circular dependencies  # noqa: E402
from smartbench.rag.chunker import CodeChunker  # noqa: E402
from smartbench.rag.embedder import CodeEmbedder  # noqa: E402
from smartbench.rag.indexer import IndexPipeline  # noqa: E402
from smartbench.rag.retriever import HybridRetriever  # noqa: E402
from smartbench.rag.store import VectorStore  # noqa: E402

__all__ = [
    "Chunk",
    "CodeChunker",
    "CodeEmbedder",
    "VectorStore",
    "IndexPipeline",
    "HybridRetriever",
]
