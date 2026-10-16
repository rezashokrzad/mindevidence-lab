"""The corpus manifest: which documents are authorized, and their bibliographic metadata.

Only files listed here are ingested. Metadata comes from the manifest, which was
transcribed from each PDF's front matter; the extractor's own findings are stored
next to it (documents.extracted_metadata) and compared, never silently merged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DOCUMENT_ID_PATTERN = r"^[A-Z0-9][A-Z0-9-]{2,63}$"


class ManifestDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    filename: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+\.pdf$")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1)
    short_title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    corporate_author: str | None = None
    publisher: str = Field(min_length=1)
    year: int | None = Field(default=None, ge=1950, le=2100)
    venue: str | None = None
    edition: str | None = None
    doi: str | None = Field(default=None, pattern=r"^10\.[0-9]{4,9}/\S+$")
    isbn: str | None = Field(default=None, pattern=r"^[0-9][0-9-]{8,15}[0-9Xx]$")
    document_type: Literal["guideline", "intervention_guide", "research_guide", "systematic_review", "other"]
    license: str | None = None
    metadata_verified: bool = False
    verification_note: str | None = None

    @model_validator(mode="after")
    def _sha_requires_file(self) -> "ManifestDocument":
        if self.sha256 and not self.filename:
            raise ValueError("sha256 given without filename")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    manifest_version: int
    documents: list[ManifestDocument]

    @field_validator("documents")
    @classmethod
    def _unique(cls, docs: list[ManifestDocument]) -> list[ManifestDocument]:
        for attr in ("document_id", "filename", "sha256"):
            values = [getattr(d, attr) for d in docs if getattr(d, attr)]
            dupes = {v for v in values if values.count(v) > 1}
            if dupes:
                raise ValueError(f"duplicate {attr} in manifest: {sorted(dupes)}")
        return docs


class ManifestError(ValueError):
    pass


def load_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from None
    try:
        return Manifest.model_validate(raw)
    except ValueError as exc:
        raise ManifestError(f"manifest is invalid: {exc}") from None
