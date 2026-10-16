"""Ingestion settings (environment variables; see backend/.env.example).

Kept separate from the API Settings so the CLI does not need sign-in secrets.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import BACKEND_DIR
from app.ingestion.chunking import ChunkingConfig

REPO_ROOT = BACKEND_DIR.parent


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    corpus_source_dir: Path = Field(default=REPO_ROOT / "data" / "papers",
                                    description="Directory scanned for source PDFs")
    corpus_manifest: Path = Field(default=REPO_ROOT / "data" / "corpus_manifest.json",
                                  description="Allow-list and bibliographic metadata of authorized documents")
    document_storage_dir: Path = Field(default=BACKEND_DIR / "storage" / "documents",
                                       description="Where original PDFs are copied (development storage)")
    chunk_target_tokens: int = 550
    chunk_min_tokens: int = 400
    chunk_max_tokens: int = 700
    chunk_overlap_tokens: int = 80

    @model_validator(mode="after")
    def _resolve(self) -> "IngestionSettings":
        # Relative paths are relative to the repository root, not the working directory.
        for name in ("corpus_source_dir", "corpus_manifest", "document_storage_dir"):
            value: Path = getattr(self, name)
            if not value.is_absolute():
                setattr(self, name, (REPO_ROOT / value).resolve())
        self.chunking  # validates the sizes
        return self

    @property
    def chunking(self) -> ChunkingConfig:
        return ChunkingConfig(self.chunk_target_tokens, self.chunk_min_tokens,
                              self.chunk_max_tokens, self.chunk_overlap_tokens)


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()
