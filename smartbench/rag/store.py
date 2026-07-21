"""
VectorStore — persistent vector storage with multiple backends.

Backends (tried in order):
  1. ChromaDB — full-featured vector DB
  2. SimpleVectorStore — numpy + json fallback (zero extra deps)

Persisted to: <project_path>/.smartbench/vector_store/
"""

from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import logging

from smartbench.rag import Chunk

logger = logging.getLogger(__name__)


class SimpleVectorStore:
    """
    Minimal numpy + json vector store — zero problematic dependencies.
    Used as fallback when ChromaDB can't be initialized.
    """

    def __init__(self, project_path: str, fingerprint_hash: str):
        self.project_path = Path(project_path)
        self.fingerprint_hash = fingerprint_hash
        self.store_path = self.project_path / ".smartbench" / "vector_store"
        self.index_file = self.store_path / f"index_{fingerprint_hash[:8]}.npz"
        self.meta_file = self.store_path / f"meta_{fingerprint_hash[:8]}.json"
        self._vectors = None  # numpy array
        self._metadata = []   # list of chunk metadata dicts

    def index_chunks(self, chunk_embedding_pairs, dimension: int) -> int:
        import numpy as np
        self.store_path.mkdir(parents=True, exist_ok=True)
        vectors = []
        meta = []
        for chunk, emb in chunk_embedding_pairs:
            vectors.append(emb)
            meta.append({
                "id": chunk.id,
                "file_path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "language": chunk.language,
                "node_type": chunk.node_type,
                "node_name": chunk.node_name,
                "content": chunk.content,
            })
        self._vectors = np.array(vectors, dtype=np.float32)
        self._metadata = meta
        np.savez_compressed(self.index_file, vectors=self._vectors)
        with open(self.meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
        return len(meta)

    def search(self, query_embedding, n_results: int = 10) -> List[Dict]:
        import numpy as np
        if self._vectors is None:
            self._load()
        if self._vectors is None or len(self._vectors) == 0:
            return []
        q = np.array(query_embedding, dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-10)
        v_norms = self._vectors / (np.linalg.norm(self._vectors, axis=1, keepdims=True) + 1e-10)
        similarities = np.dot(v_norms, q_norm)
        top_k = min(n_results, len(similarities))
        indices = np.argsort(-similarities)[:top_k]
        results = []
        for idx in indices:
            meta = self._metadata[int(idx)]
            results.append({
                "id": meta["id"],
                "content": meta["content"],
                "metadata": {k: v for k, v in meta.items() if k != "content"},
                "distance": float(1.0 - similarities[idx]),
                "score": float(max(0.0, similarities[idx])),
            })
        return results

    def search_by_text(self, query, embedder, n_results=10):
        q_emb = embedder.embed_query(query)
        if not q_emb:
            return []
        return self.search(q_emb, n_results)

    def count(self) -> int:
        if self._vectors is not None:
            return len(self._vectors)
        if self.index_file.exists():
            return len(self._load_meta_only())
        return 0

    def clear(self):
        if self.index_file.exists():
            self.index_file.unlink()
        if self.meta_file.exists():
            self.meta_file.unlink()
        self._vectors = None
        self._metadata = []

    def exists(self) -> bool:
        return self.index_file.exists() and self.meta_file.exists()

    def _load(self):
        import numpy as np
        if not self.index_file.exists():
            return
        try:
            data = np.load(self.index_file)
            self._vectors = data["vectors"]
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                self._metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load vector index: {e}")

    def _load_meta_only(self) -> list:
        try:
            with open(self.meta_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []


class VectorStore:
    """
    Vector store with automatic backend selection.

    Tries ChromaDB first, falls back to SimpleVectorStore (numpy + json)
    if ChromaDB has dependency issues (common on bleeding-edge Python).

    Usage:
        store = VectorStore("/path/to/project", "abc123")
        store.index_chunks(chunk_embedding_pairs, dimension=384)
        results = store.search_by_text("error handling", embedder)
    """

    def __init__(self, project_path: str, fingerprint_hash: str):
        """
        Args:
            project_path: Root directory of the project
            fingerprint_hash: Stable hash from ProjectFingerprint
        """
        self.project_path = Path(project_path)
        self.fingerprint_hash = fingerprint_hash
        self.store_path = self.project_path / ".smartbench" / "vector_store"
        self.collection_name = f"code_{fingerprint_hash[:8]}"
        self._client = None
        self._collection = None
        self._backend = None  # "chromadb" or "simple"
        self._simple_store = None

    # ── Public API ──────────────────────────────────────────────────────

    def index_chunks(self,
                     chunk_embedding_pairs: List[Tuple[Chunk, List[float]]],
                     dimension: int) -> int:
        """
        Store chunks and their embeddings.
        Uses upsert (insert or update) so re-indexing is idempotent.
        """
        if not chunk_embedding_pairs:
            return 0

        if self._backend is None:
            self._init_backend()

        if self._backend == "simple":
            return self._simple_store.index_chunks(chunk_embedding_pairs, dimension)

        return self._index_chunks_chroma(chunk_embedding_pairs, dimension)

    def search(self, query_embedding: List[float],
               n_results: int = 10) -> List[Dict]:
        """Search by embedding vector."""
        if self._backend is None:
            self._init_backend()

        if self._backend == "simple":
            return self._simple_store.search(query_embedding, n_results)
        return self._search_chroma(query_embedding, n_results)

    def search_by_text(self, query: str, embedder,
                       n_results: int = 10) -> List[Dict]:
        """Convenience: embed query text then search."""
        if self._backend is None:
            self._init_backend()

        if self._backend == "simple":
            return self._simple_store.search_by_text(query, embedder, n_results)
        return self._search_by_text_chroma(query, embedder, n_results)

    def count(self) -> int:
        """Get number of indexed chunks."""
        if self._backend == "simple" and self._simple_store:
            return self._simple_store.count()
        if self._backend is None:
            # Check SimpleVectorStore
            svs = SimpleVectorStore(str(self.project_path), self.fingerprint_hash)
            return svs.count()
        try:
            self._ensure_chroma_collection(1)
            return self._collection.count() if self._collection else 0
        except Exception:
            return 0

    def clear(self):
        """Delete the collection (for re-indexing)."""
        # Always clear SimpleVectorStore
        SimpleVectorStore(str(self.project_path), self.fingerprint_hash).clear()
        # Also try ChromaDB if we have a client
        if self._client is not None:
            try:
                self._client.delete_collection(self.collection_name)
            except Exception:
                pass
        self._collection = None

    def exists(self) -> bool:
        """Check if the index already exists (uses SimpleVectorStore check)."""
        svs = SimpleVectorStore(str(self.project_path), self.fingerprint_hash)
        return svs.exists()

    def save_tfidf_vocab(self, vectorizer) -> None:
        """Persist TF-IDF vectorizer vocabulary for later reload."""
        import pickle
        try:
            self.store_path.mkdir(parents=True, exist_ok=True)
            vocab_path = self.store_path / f"tfidf_vocab_{self.fingerprint_hash[:8]}.pkl"
            with open(vocab_path, 'wb') as f:
                pickle.dump({
                    'vocabulary': vectorizer.vocabulary_,
                    'idf': vectorizer.idf_.tolist() if hasattr(vectorizer, 'idf_') else [],
                    'max_features': getattr(vectorizer, 'max_features', 256),
                }, f)
            logger.info("Saved TF-IDF vocab to %s", vocab_path)
        except Exception as e:
            logger.warning("Failed to save TF-IDF vocab: %s", e)

    def load_tfidf_vocab(self) -> Optional[Dict]:
        """Load persisted TF-IDF vocabulary for query embedding."""
        import pickle
        vocab_path = self.store_path / f"tfidf_vocab_{self.fingerprint_hash[:8]}.pkl"
        if not vocab_path.exists():
            return None
        try:
            with open(vocab_path, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning("Failed to load TF-IDF vocab: %s", e)
            return None

    # ── Backend initialization ──────────────────────────────────────────

    def _init_backend(self):
        """
        Initialize the storage backend.

        Uses SimpleVectorStore (numpy+json) as the DEFAULT because it:
          - Has zero C-extension dependencies (no segfault risk)
          - Works reliably on all Python versions
          - Is fast enough for projects up to ~10K chunks

        ChromaDB is available as an optional upgrade via
        prefer_chromadb=True (set at init or via env var).
        """
        # Use SimpleVectorStore by default (safest, zero C deps)
        self._backend = "simple"
        self._simple_store = SimpleVectorStore(
            str(self.project_path), self.fingerprint_hash
        )
        logger.info("Using SimpleVectorStore (numpy+json) backend")

        # Optionally try ChromaDB if env var is set
        import os
        if os.environ.get("SMARTBENCH_CHROMADB"):
            try:
                self._ensure_chroma_client()
                test_col = self._client.create_collection(
                    name=f"_test_{self.fingerprint_hash[:4]}",
                    embedding_function=None,
                )
                self._client.delete_collection(test_col.name)
                self._backend = "chromadb"
                self._simple_store = None
                logger.info("Switched to ChromaDB backend")
            except Exception as e:
                logger.info(f"ChromaDB unavailable ({e}), keeping SimpleVectorStore")

    # ── ChromaDB backend ────────────────────────────────────────────────

    def _ensure_chroma_client(self):
        """Lazy init ChromaDB persistent client."""
        import chromadb
        self.store_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.store_path))

    def _ensure_chroma_collection(self, dimension: int):
        """Get or create the ChromaDB collection."""
        if self._client is None:
            self._ensure_chroma_client()
        if self._collection is not None:
            return
        try:
            self._collection = self._client.get_collection(
                self.collection_name, embedding_function=None
            )
        except Exception:
            self._collection = self._client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )

    def _index_chunks_chroma(self, chunk_embedding_pairs, dimension: int) -> int:
        self._ensure_chroma_collection(dimension)
        ids = []
        embeddings = []
        metadatas = []
        documents = []
        for chunk, embedding in chunk_embedding_pairs:
            ids.append(chunk.id)
            embeddings.append(embedding)
            metadatas.append({
                "file_path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "language": chunk.language,
                "node_type": chunk.node_type,
                "node_name": chunk.node_name,
            })
            documents.append(chunk.content)
        try:
            self._collection.upsert(
                ids=ids, embeddings=embeddings,
                metadatas=metadatas, documents=documents,
            )
            return len(ids)
        except Exception as e:
            logger.error(f"ChromaDB index failed: {e}")
            return 0

    def _search_chroma(self, query_embedding, n_results=10) -> List[Dict]:
        self._ensure_chroma_collection(len(query_embedding))
        try:
            results = self._collection.query(
                query_embeddings=[query_embedding], n_results=n_results,
                include=["metadatas", "documents", "distances"],
            )
            return self._format_results(results)
        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []

    def _search_by_text_chroma(self, query, embedder, n_results=10) -> List[Dict]:
        q_emb = embedder.embed_query(query)
        if not q_emb:
            return []
        return self._search_chroma(q_emb, n_results)

    @staticmethod
    def _format_results(raw) -> List[Dict]:
        """Format ChromaDB query results into uniform dicts."""
        results = []
        if not raw.get("ids") or not raw["ids"][0]:
            return results
        ids = raw["ids"][0]
        documents = raw.get("documents", [[]])[0] if raw.get("documents") else []
        metadatas = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []
        distances = raw.get("distances", [[]])[0] if raw.get("distances") else []
        for i in range(len(ids)):
            distance = distances[i] if i < len(distances) else 1.0
            results.append({
                "id": ids[i],
                "content": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distance,
                "score": max(0.0, 1.0 - distance),
            })
        return results
