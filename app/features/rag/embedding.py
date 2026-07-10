from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.core.config import settings


EMBEDDING_DIMENSIONS = 768
DEFAULT_EMBEDDING_MODEL = settings.RAG_EMBEDDING_MODEL


class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingConfigurationError(RuntimeError):
    pass


def validate_embedding(vector: list[float]) -> list[float]:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding must have exactly {EMBEDDING_DIMENSIONS} dimensions."
        )
    return vector


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingConfigurationError(
                "sentence-transformers is not installed. Install it before using "
                "the production RAG embedding provider."
            ) from exc
        self._model = SentenceTransformer(self.model_name)
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [validate_embedding([float(value) for value in row]) for row in embeddings]


class HashEmbeddingProvider:
    """Deterministic test/dev fallback; never use for production retrieval quality."""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(text) for text in texts]


def _hash_embedding(text: str) -> list[float]:
    values: list[float] = []
    counter = 0
    while len(values) < EMBEDDING_DIMENSIONS:
        digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)
        counter += 1
    vector = values[:EMBEDDING_DIMENSIONS]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
