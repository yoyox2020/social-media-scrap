"""
BGE-M3 Embedding Generator — lazy-loaded, singleton per worker process.

Model: BAAI/bge-m3
Output: 1024-dimensional normalized float vector (cocok dengan pgvector VECTOR(1024))

Normalisasi dengan normalize_embeddings=True menghasilkan unit vector sehingga
cosine similarity = dot product — lebih efisien untuk pgvector <=> operator.
"""
from __future__ import annotations

from app.shared.constants import EMBEDDING_DIMENSION

_ZERO_VECTOR: list[float] = [0.0] * EMBEDDING_DIMENSION


class EmbeddingGenerator:
    _instance: EmbeddingGenerator | None = None

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None):
        from sentence_transformers import SentenceTransformer

        from app.shared.config import settings

        self.model_name = model_name or settings.bge_m3_model_name
        self._model = SentenceTransformer(
            self.model_name,
            cache_folder=cache_dir or settings.models_cache_dir,
        )

    @classmethod
    def get_instance(cls) -> "EmbeddingGenerator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def generate(self, text: str) -> list[float]:
        """Generate embedding untuk satu teks. Return list float 1024 dim."""
        if not text or not text.strip():
            return _ZERO_VECTOR

        embedding = self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        result = embedding.tolist()
        assert len(result) == EMBEDDING_DIMENSION, (
            f"Expected {EMBEDDING_DIMENSION} dims, got {len(result)}"
        )
        return result

    def generate_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Generate embedding batch. Lebih efisien untuk banyak teks sekaligus."""
        if not texts:
            return []

        # Replace None/empty dengan placeholder agar batch tetap aligned
        safe_texts = [t if t and t.strip() else " " for t in texts]
        embeddings = self._model.encode(
            safe_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        results = []
        for i, emb in enumerate(embeddings):
            if not texts[i] or not texts[i].strip():
                results.append(_ZERO_VECTOR)
            else:
                results.append(emb.tolist())
        return results
