"""Embedding-model interface and implementations.

An Embedder turns texts into L2-normalised vectors (so cosine distance = 1 - dot product)
and reports the exact model identity that is stored next to every vector.

* SentenceTransformerEmbedder: the real model (default all-MiniLM-L6-v2, 384 dimensions).
  The `sentence-transformers` package and the model weights are loaded lazily on first use;
  the weights are downloaded by Hugging Face on first run (about 90 MB).
* HashEmbedder: a deterministic bag-of-words hashing embedder. It needs no download and
  makes tests and offline development reproducible. It is NOT semantic: it only matches
  shared words, so never use it to judge real retrieval quality.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from app.retrieval.config import RetrievalSettings


class EmbeddingError(RuntimeError):
    """The embedder failed or returned unusable output."""


@runtime_checkable
class Embedder(Protocol):
    name: str
    version: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalised vector of length `dimension` per text, in order."""
        ...


def model_id(embedder: Embedder) -> str:
    return f"{embedder.name}@{embedder.version}#{embedder.dimension}"


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0 or not math.isfinite(norm):
        raise EmbeddingError("embedding has zero or non-finite norm")
    return [v / norm for v in vec]


def validate_vectors(vectors: list[list[float]], expected: int, dimension: int) -> list[list[float]]:
    """Reject wrong counts, wrong dimensions, and non-finite values before they reach the database."""
    if len(vectors) != expected:
        raise EmbeddingError(f"embedder returned {len(vectors)} vectors for {expected} texts")
    for v in vectors:
        if len(v) != dimension:
            raise EmbeddingError(f"embedding has dimension {len(v)}, expected {dimension}")
        if not all(math.isfinite(x) for x in v):
            raise EmbeddingError("embedding contains NaN or infinity")
    return vectors


class HashEmbedder:
    name = "hash-bow"
    version = "1"

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dimension
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dimension
                vec[idx] += 1.0
            if not any(vec):
                vec[0] = 1.0
            out.append(_normalize(vec))
        return out


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, revision: str = "", dimension: int = 384):
        self.name = model_name
        self.dimension = dimension
        self._revision = revision or None
        self._model = None
        self._version: str | None = None

    def _load(self):
        if self._model is None:
            try:
                import sentence_transformers
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed. Run: pip install -r requirements-embeddings.txt") from exc
            try:
                self._model = SentenceTransformer(self.name, revision=self._revision)
            except Exception as exc:  # network, disk, unknown model
                raise EmbeddingError(f"could not load embedding model {self.name!r}: {exc}") from exc
            actual = (getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension)()
            if actual != self.dimension:
                raise EmbeddingError(f"model {self.name!r} has dimension {actual}, expected {self.dimension}")
            commit = (getattr(getattr(self._model[0].auto_model, "config", None), "_commit_hash", None)
                      or self._cached_commit() or self._revision)
            self._version = f"rev-{commit or 'unknown'}+st-{sentence_transformers.__version__}"
        return self._model

    def _cached_commit(self) -> str | None:
        """The Hugging Face snapshot directory is named after the exact commit that was downloaded."""
        try:
            from pathlib import Path

            from huggingface_hub import snapshot_download

            name = Path(snapshot_download(self.name, local_files_only=True)).name
            return name if len(name) >= 12 and all(c in "0123456789abcdef" for c in name) else None
        except Exception:
            return None

    @property
    def version(self) -> str:
        self._load()
        return self._version

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            arr = model.encode(texts, batch_size=len(texts) or 1, normalize_embeddings=True,
                               convert_to_numpy=True, show_progress_bar=False)
        except Exception as exc:
            raise EmbeddingError(f"encoding failed: {exc}") from exc
        return [list(map(float, row)) for row in arr]


def build_embedder(settings: RetrievalSettings) -> Embedder:
    if settings.embedding_backend == "hash":
        return HashEmbedder(settings.embedding_dimension)
    return SentenceTransformerEmbedder(settings.embedding_model_name, settings.embedding_model_revision,
                                       settings.embedding_dimension)
