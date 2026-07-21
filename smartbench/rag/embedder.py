"""
CodeEmbedder — local sentence-transformers embedding wrapper.

Uses intfloat/multilingual-e5-small by default:
  - 384-dimensional embeddings
  - Handles Chinese + code
  - ~120MB download, cached locally
  - CPU inference, no GPU required

Falls back to all-MiniLM-L6-v2 if E5 download fails.
"""

import logging
from typing import Dict, List, Optional, Tuple

from smartbench.rag import Chunk

logger = logging.getLogger(__name__)


class CodeEmbedder:
    """
    Local embedding model wrapper. Lazy-loads on first use.

    Usage:
        embedder = CodeEmbedder()
        vectors = embedder.embed(["def foo(): pass", "class Bar:"])
        chunk_vecs = embedder.embed_chunks(chunks)
    """

    # Primary: multilingual, handles Chinese comments + code
    DEFAULT_MODEL = "intfloat/multilingual-e5-small"
    # Fallback: English-only, smaller, more reliable download
    FALLBACK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: Optional[str] = None,
                 device: str = "cpu"):
        """
        Args:
            model_name: HuggingFace model ID. None = use default.
            device: "cpu" or "cuda"
        """
        self.model_name = model_name or self.DEFAULT_MODEL
        self.device = device
        self._model = None
        self._dimension = None
        self._load_attempted = False
        self._load_failed = False
        self._fallback_mode = None  # "tfidf" if using sklearn fallback
        self._tfidf_vectorizer = None  # fitted TF-IDF vectorizer
        self._tfidf_dim = 256  # fixed dimension for TF-IDF

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def dimension(self) -> int:
        """Get embedding dimension."""
        if self._dimension is None:
            try:
                self._load_model()
            except Exception:
                pass
        return self._dimension or 384

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Embed a list of text strings.

        For TF-IDF fallback: fits on texts for indexing, uses saved
        vocabulary for queries.
        """
        if not texts:
            return []

        if self._model is None and not self._load_attempted:
            try:
                self._load_model()
            except Exception:
                self._load_failed = True

        # TF-IDF fallback mode
        if self._fallback_mode == "tfidf":
            return self._embed_tfidf(texts)

        # No model available
        if self._load_failed or self._model is None:
            dim = self._dimension or 384
            return [[0.0] * dim for _ in texts]

        # Standard sentence-transformers path
        try:
            embeddings = self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            dim = self._dimension or 384
            return [[0.0] * dim for _ in texts]

    def embed_chunks(self, chunks: List[Chunk]) -> List[Tuple[Chunk, List[float]]]:
        """
        Embed chunk contents. For TF-IDF: fits vectorizer on all chunks.

        Args:
            chunks: List of Chunk objects

        Returns:
            List of (Chunk, embedding_vector) tuples
        """
        is_e5 = "e5" in self.model_name.lower()

        texts = []
        for c in chunks:
            if is_e5:
                texts.append(f"passage: {c.content}")
            else:
                texts.append(c.content)

        # For TF-IDF, fit on the full chunk corpus (once)
        if self._fallback_mode == "tfidf" and self._tfidf_vectorizer is None:
            self._fit_tfidf(texts)

        embeddings = self.embed(texts)
        return list(zip(chunks, embeddings))

    def embed_query(self, query: str) -> List[float]:
        """
        Embed a search query with consistent dimensions.

        CRITICAL: Must pre-load model to determine fallback mode BEFORE
        calling embed(), so we can route to the correct embedding method.
        """
        # Ensure model/mode is loaded first
        if self._model is None and not self._load_attempted:
            try:
                self._load_model()
            except Exception:
                self._load_failed = True

        is_e5 = "e5" in self.model_name.lower()
        text = f"query: {query}" if is_e5 else query

        if self._fallback_mode == "tfidf":
            if self._tfidf_vectorizer is not None:
                # Use fitted vocabulary from index build (exact match)
                from sklearn.preprocessing import normalize
                vec = self._tfidf_vectorizer.transform([text])
                return normalize(vec, norm='l2').toarray()[0].tolist()
            else:
                # No fitted vocab — use stable char hashing (ALWAYS _tfidf_dim)
                return self._embed_tfidf_fallback([text])[0]

        # Standard sentence-transformers path
        if self._load_failed or self._model is None:
            return [0.0] * (self._dimension or 384)
        try:
            emb = self._model.encode([text], normalize_embeddings=True)
            return emb[0].tolist()
        except Exception:
            return [0.0] * (self._dimension or 384)

    # ── Internals ───────────────────────────────────────────────────────

    def _load_model(self):
        """Lazy-load the sentence-transformers model."""
        self._load_attempted = True
        errors = []

        # Pre-check: can we import torch? If not, skip sentence-transformers entirely
        can_use_st = True
        try:
            import torch  # noqa: F401
        except Exception as e:
            can_use_st = False
            errors.append(f"torch unavailable: {e}")
            logger.warning("PyTorch not available, skipping sentence-transformers")

        # Try primary model (only if torch works)
        if can_use_st:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading embedding model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name, device=self.device)
                self._dimension = self._model.get_sentence_embedding_dimension()
                logger.info(f"Model loaded. Dimension: {self._dimension}")
                return
            except Exception as e:
                errors.append(f"{self.model_name}: {e}")
                logger.warning(f"Failed to load {self.model_name}: {e}")

            # Try fallback model
            if self.model_name != self.FALLBACK_MODEL:
                try:
                    from sentence_transformers import SentenceTransformer
                    logger.info(f"Trying fallback: {self.FALLBACK_MODEL}")
                    self.model_name = self.FALLBACK_MODEL
                    self._model = SentenceTransformer(self.model_name, device=self.device)
                    self._dimension = self._model.get_sentence_embedding_dimension()
                    return
                except Exception as e:
                    errors.append(f"{self.FALLBACK_MODEL}: {e}")
                    logger.warning(f"Fallback also failed: {e}")

        # All sentence-transformers failed. Try sklearn TF-IDF as last resort
        try:
            logger.info("Using sklearn TF-IDF as fallback embedder")
            self._fallback_mode = "tfidf"
            self._dimension = self._tfidf_dim  # 256, consistent
            return
        except Exception as e:
            errors.append(f"sklearn TF-IDF: {e}")

        # Nothing works
        self._load_failed = True
        self._dimension = 384
        raise RuntimeError(
            f"Failed to load any embedding backend. Errors: {'; '.join(errors)}"
        ) from None

    def is_available(self) -> bool:
        """Check if the embedder can be initialized."""
        if self._load_failed:
            return False
        try:
            self._load_model()
            return self._model is not None or self._fallback_mode is not None
        except Exception:
            self._load_failed = True
            return False

    # ── TF-IDF fallback ────────────────────────────────────────────────

    def _fit_tfidf(self, texts: List[str]):
        """
        Fit TF-IDF vectorizer on the chunk corpus. Called once during indexing.
        Saves the fitted vectorizer for consistent query transformations.
        """
        from sklearn.feature_extraction.text import TfidfVectorizer

        logger.info(f"Fitting TF-IDF on {len(texts)} documents")
        self._tfidf_vectorizer = TfidfVectorizer(
            max_features=self._tfidf_dim,
            analyzer='char_wb',
            ngram_range=(2, 4),
        )
        self._tfidf_vectorizer.fit(texts)
        self._dimension = self._tfidf_dim

    def _load_tfidf_vocab(self, vocab_data: Dict) -> None:
        """Reload TF-IDF vocabulary from persisted data (for query reuse)."""
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        vocabulary = vocab_data.get('vocabulary', {})
        if not vocabulary:
            return

        max_features = vocab_data.get('max_features', self._tfidf_dim)
        self._tfidf_vectorizer = TfidfVectorizer(
            max_features=max_features,
            analyzer='char_wb',
            ngram_range=(2, 4),
            vocabulary=vocabulary,
        )
        # Set idf_ manually if available
        idf_list = vocab_data.get('idf', [])
        if idf_list:
            self._tfidf_vectorizer.idf_ = np.array(idf_list)
        self._dimension = max_features
        self._fallback_mode = 'tfidf'
        logger.info("Loaded TF-IDF vocab: %d terms", len(vocabulary))

    def _embed_tfidf(self, texts: List[str]) -> List[List[float]]:
        """
        TF-IDF embedding for INDEXING ONLY (bulk chunk embedding).
        Fits vocabulary on the full corpus for consistent dimensions.

        NOT for queries — use embed_query() which uses _embed_tfidf_fallback
        when no fitted vocabulary exists.
        """
        from sklearn.preprocessing import normalize

        if self._tfidf_vectorizer is not None:
            # Already fitted — just transform
            matrix = self._tfidf_vectorizer.transform(texts)
        else:
            # First call during indexing: fit on full chunk corpus
            from sklearn.feature_extraction.text import TfidfVectorizer
            logger.info(f"Fitting TF-IDF on {len(texts)} documents")
            self._tfidf_vectorizer = TfidfVectorizer(
                max_features=self._tfidf_dim,
                analyzer='char_wb',
                ngram_range=(2, 4),
            )
            matrix = self._tfidf_vectorizer.fit_transform(texts)
            self._dimension = matrix.shape[1]  # actual feature count
            logger.info(f"TF-IDF fitted: {matrix.shape[1]} features")

        try:
            normalized = normalize(matrix, norm='l2')
            return normalized.toarray().tolist()
        except Exception as e:
            logger.error(f"TF-IDF embedding failed: {e}")
            return [[0.0] * max(1, matrix.shape[1]) for _ in texts]

    def _embed_tfidf_fallback(self, texts: List[str]) -> List[List[float]]:
        """
        Character n-gram embedding without pre-fitted vocabulary.
        Used for ad-hoc queries when no index has been built.
        """
        import numpy as np

        results = []
        for text in texts:
            # Simple character-level feature vector
            vec = np.zeros(self._tfidf_dim, dtype=np.float32)
            chars = list(text.lower())
            for i, ch in enumerate(chars):
                idx = ord(ch) % self._tfidf_dim
                vec[idx] += 1.0 / (i + 1)  # position-weighted
            # Normalize
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            results.append(vec.tolist())
        return results
