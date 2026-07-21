"""
IndexPipeline — orchestrate project indexing: discover → chunk → embed → store.

Usage:
    pipeline = IndexPipeline(project_path, fingerprint)
    store = pipeline.index(graph)         # Full build
    store = pipeline.index_if_needed(graph)  # Incremental, skip if fresh
"""

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

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

    INDEX_SCHEMA_VERSION = 3

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

        # Stable storage identity. Source changes are tracked separately so an
        # edited project overwrites its old index instead of leaking new files.
        project_identity = str(Path(project_path).resolve())
        self.fingerprint_hash = hashlib.sha256(project_identity.encode()).hexdigest()

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
        source_hash = self._calculate_source_hash()

        # Step 1: Chunk
        logger.info("Chunking project files...")
        chunks = self.chunker.chunk_project(self.project_path, graph)
        logger.info(f"Created {len(chunks)} code chunks")

        store = VectorStore(self.project_path, self.fingerprint_hash)
        store.clear()

        if not chunks:
            return store, self.embedder

        # Step 2: Embed (fits vocabulary on chunks)
        logger.info(f"Embedding {len(chunks)} chunks...")
        embedded = self.embedder.embed_chunks(chunks)

        # Step 3: Store
        logger.info("Storing embeddings...")
        stored = store.index_chunks(embedded, self.embedder.dimension)
        if stored != len(chunks):
            store.clear()
            raise RuntimeError(
                f"Vector index incomplete: stored {stored} of {len(chunks)} chunks"
            )

        # Persist TF-IDF vocabulary for later query reuse
        if self.embedder._fallback_mode == "tfidf" and self.embedder._tfidf_vectorizer:
            store.save_tfidf_vocab(self.embedder._tfidf_vectorizer)

        store.save_index_state({
            **self._expected_index_state(source_hash),
            "embedding_backend": self.embedder._fallback_mode or "sentence-transformers",
            "embedding_model": self.embedder.model_name,
            "embedding_dimension": self.embedder.dimension,
            "storage_backend": store._backend,
        })

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
                self._restore_embedder_state(store.load_index_state() or {})
                return store, self.embedder
            logger.info("Vector index stale, rebuilding...")

        return self.index(graph)

    def needs_reindex(self, graph: Optional[CodeGraph] = None) -> bool:
        """
        Check if the index is stale or doesn't exist yet using source hashes.

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

        Compares an exact source-content digest and index schema metadata.
        """
        try:
            if store.count() == 0:
                return True
            state = store.load_index_state()
            if not state:
                return True
            if (
                state.get("embedding_backend") == "tfidf"
                and not store.load_tfidf_vocab()
            ):
                return True
            expected = self._expected_index_state(self._calculate_source_hash())
            return any(state.get(key) != value for key, value in expected.items())
        except Exception as e:
            logger.warning(f"Index health check failed: {e}, will rebuild")
            return True

    def _calculate_source_hash(self) -> str:
        """Hash every indexable file so same-size edits also invalidate cache."""
        digest = hashlib.sha256()
        root = Path(self.project_path)
        for relative, full_path in sorted(self.chunker._discover_files(root)):
            digest.update(relative.encode("utf-8", errors="surrogatepass"))
            digest.update(b"\0")
            try:
                with open(full_path, "rb") as handle:
                    for block in iter(lambda: handle.read(128 * 1024), b""):
                        digest.update(block)
            except OSError as exc:
                digest.update(f"unreadable:{exc.__class__.__name__}".encode())
            digest.update(b"\0")
        return digest.hexdigest()

    def _expected_index_state(self, source_hash: str) -> dict:
        return {
            "schema_version": self.INDEX_SCHEMA_VERSION,
            "source_hash": source_hash,
            "chunk_size": self.chunker.chunk_size,
            "overlap": self.chunker.overlap,
            "max_chunk_chars": self.chunker.max_chunk_chars,
        }

    def _restore_embedder_state(self, state: dict) -> None:
        """Keep query vectors compatible with the persisted index backend."""
        backend = state.get("embedding_backend")
        dimension = state.get("embedding_dimension")
        if isinstance(dimension, int) and dimension > 0:
            self.embedder._dimension = dimension
        if backend == "hash":
            self.embedder._fallback_mode = "hash"
            self.embedder._load_attempted = True
        model_name = state.get("embedding_model")
        if isinstance(model_name, str) and model_name:
            self.embedder.model_name = model_name
