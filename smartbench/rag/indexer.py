"""
IndexPipeline — orchestrate project indexing: discover → chunk → embed → store.

Usage:
    pipeline = IndexPipeline(project_path, fingerprint)
    store = pipeline.index(graph)         # Full build
    store = pipeline.index_if_needed(graph)  # Incremental, skip if fresh
"""

from pathlib import Path
from typing import Optional
import hashlib
import logging
import time

from smartbench.detector.fingerprint import ProjectFingerprint
from smartbench.graph.schema import CodeGraph
from smartbench.rag.chunker import CodeChunker
from smartbench.rag.embedder import CodeEmbedder
from smartbench.rag.store import VectorStore

logger = logging.getLogger(__name__)


class IndexPipeline:
    """
    Orchestrates the full indexing pipeline.

    Steps:
      1. Chunk all source files (using graph nodes for structure)
      2. Embed every chunk
      3. Store in ChromaDB

    The index is persisted to <project_path>/.smartbench/vector_store/
    and can be reused across SmartBench sessions.
    """

    def __init__(self, project_path: str,
                 fingerprint: ProjectFingerprint,
                 chunker: Optional[CodeChunker] = None,
                 embedder: Optional[CodeEmbedder] = None):
        """
        Args:
            project_path: Root directory of the project
            fingerprint: ProjectFingerprint from scanner
            chunker: Custom chunker (uses defaults if None)
            embedder: Custom embedder (uses defaults if None)
        """
        self.project_path = project_path
        self.fingerprint = fingerprint
        self.chunker = chunker or CodeChunker()
        self.embedder = embedder or CodeEmbedder()

        # Stable hash for the project (fingerprint summary + path)
        fp_str = fingerprint.summary() + str(Path(project_path).resolve())
        self.fingerprint_hash = hashlib.md5(fp_str.encode()).hexdigest()

    # ── Public API ──────────────────────────────────────────────────────

    def index(self, graph: Optional[CodeGraph] = None) -> tuple:
        """
        Full indexing pipeline: chunk → embed → store.

        Args:
            graph: Optional CodeGraph for structural boundary detection

        Returns:
            (VectorStore, CodeEmbedder) tuple — store is ready for querying,
            embedder has the fitted vocabulary (must be reused for queries!)
        """
        start = time.time()

        # Step 1: Chunk
        logger.info("Chunking project files...")
        chunks = self.chunker.chunk_project(self.project_path, graph)
        logger.info(f"Created {len(chunks)} code chunks")

        if not chunks:
            store = VectorStore(self.project_path, self.fingerprint_hash)
            return store, self.embedder

        # Step 2: Embed (fits vocabulary on chunks)
        logger.info(f"Embedding {len(chunks)} chunks...")
        embedded = self.embedder.embed_chunks(chunks)

        # Step 3: Store
        logger.info("Storing embeddings...")
        store = VectorStore(self.project_path, self.fingerprint_hash)
        stored = store.index_chunks(embedded, self.embedder.dimension)

        # Persist TF-IDF vocabulary for later query reuse
        if self.embedder._fallback_mode == "tfidf" and self.embedder._tfidf_vectorizer:
            store.save_tfidf_vocab(self.embedder._tfidf_vectorizer)

        elapsed = time.time() - start
        logger.info(f"Indexing complete: {stored} chunks in {elapsed:.1f}s")

        return store, self.embedder

    def index_if_needed(self, graph: Optional[CodeGraph] = None,
                        force: bool = False) -> tuple:
        """
        Build index only if stale or not yet built.

        Returns:
            (VectorStore, CodeEmbedder) — embedder has fitted vocabulary
        """
        store = VectorStore(self.project_path, self.fingerprint_hash)

        if not force and store.exists():
            if not self._needs_rebuild(store, graph):
                logger.info("Using existing vector index (up-to-date)")
                # Reload TF-IDF vocabulary if using fallback mode
                tfidf_data = store.load_tfidf_vocab()
                if tfidf_data:
                    self.embedder._load_tfidf_vocab(tfidf_data)
                return store, self.embedder
            logger.info("Vector index stale, rebuilding...")

        return self.index(graph)

    def needs_reindex(self, graph: Optional[CodeGraph] = None) -> bool:
        """
        Check if the index is stale or doesn't exist yet.
        Uses lightweight file-stat heuristic — does NOT chunk the project.

        Returns True if re-indexing is needed.
        """
        store = VectorStore(self.project_path, self.fingerprint_hash)
        if not store.exists():
            return True
        return self._needs_rebuild(store, graph)

    # ── Internals ───────────────────────────────────────────────────────

    def _needs_rebuild(self, store: VectorStore,
                       graph: Optional[CodeGraph]) -> bool:
        """
        Determine if the index needs rebuilding.

        Uses the graph node count as a lightweight proxy for expected chunks
        (avoids expensive full-project chunking just to check staleness).
        """
        try:
            stored_count = store.count()
            if stored_count == 0:
                return True

            # Estimate expected chunks from graph nodes (cheap heuristic)
            # Each graph node ≈ 1 function/class/file chunk
            expected = len(graph.nodes) if graph else 50
            # Add ~20% for non-graph files (configs, docs, etc.)
            expected = int(expected * 1.2)

            if stored_count < expected * 0.6:
                logger.info(
                    f"Index may be stale: {stored_count} stored vs ~{expected} expected"
                )
                return True

            return False
        except Exception as e:
            logger.warning(f"Index health check failed: {e}, will rebuild")
            return True
