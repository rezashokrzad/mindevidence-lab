"""Retrieval and embedding settings (environment variables; see backend/.env.example)."""
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fixed by the migration: corpus.chunk_embeddings.embedding is vector(384).
VECTOR_DIMENSION = 384


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True, hide_input_in_errors=True)

    embedding_backend: Literal["sentence-transformers", "hash"] = "sentence-transformers"
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Hugging Face revision (commit hash) to pin. Empty = the revision that gets downloaded is
    # recorded as the version, but is not pinned. Pin it for reproducible indexes.
    embedding_model_revision: str = ""
    embedding_dimension: int = VECTOR_DIMENSION
    embedding_preload: bool = False
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_max_retries: int = Field(default=3, ge=0, le=10)
    embedding_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=60)
    # Windows: a chunk is split into overlapping windows of at most this many words,
    # because the model reads only ~256 word-piece tokens (about 150-180 English words).
    embedding_window_words: int = Field(default=150, ge=40, le=400)
    embedding_window_overlap_words: int = Field(default=30, ge=0, le=100)

    retrieval_default_top_k: int = Field(default=5, ge=1, le=50)
    retrieval_max_top_k: int = Field(default=20, ge=1, le=50)
    retrieval_default_min_similarity: float | None = Field(default=None, ge=-1, le=1)

    @model_validator(mode="after")
    def _check(self) -> "RetrievalSettings":
        if self.embedding_dimension != VECTOR_DIMENSION:
            raise ValueError(
                f"EMBEDDING_DIMENSION must be {VECTOR_DIMENSION} (the database column is vector({VECTOR_DIMENSION})); "
                "another dimension needs a new migration and a full re-embed")
        if self.embedding_window_overlap_words >= self.embedding_window_words // 2:
            raise ValueError("window overlap must be under half the window size")
        if self.retrieval_default_top_k > self.retrieval_max_top_k:
            raise ValueError("RETRIEVAL_DEFAULT_TOP_K must not exceed RETRIEVAL_MAX_TOP_K")
        return self
