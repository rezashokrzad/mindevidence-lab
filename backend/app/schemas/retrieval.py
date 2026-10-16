"""Semantic retrieval request/response schemas."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.documents import DOCUMENT_ID_PATTERN, DocumentType

Topic = Literal["alcohol", "cannabis", "opioids", "stimulants", "behavioral"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [{"query": "How long should drug addiction treatment last?", "top_k": 5,
                      "filters": {"topics": ["opioids"]}}]})

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    top_k: int | None = Field(default=None, ge=1, le=50, description="Defaults to RETRIEVAL_DEFAULT_TOP_K; capped by RETRIEVAL_MAX_TOP_K")
    min_similarity: float | None = Field(
        default=None, ge=-1, le=1,
        description="Drop results below this cosine similarity. A cut-off for noise, not a correctness threshold.")
    filters: "SearchFilters | None" = None


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[Annotated[str, StringConstraints(pattern=DOCUMENT_ID_PATTERN)]] | None = Field(
        default=None, max_length=20)
    document_types: list[DocumentType] | None = Field(default=None, max_length=5)
    topics: list[Topic] | None = Field(
        default=None, max_length=5,
        description="Lexical filter: keeps chunks whose text mentions any term of the topic (e.g. 'opioid', "
                    "'heroin', 'methadone' for opioids). A keyword filter, not a curated label.")


SearchRequest.model_rebuild()


class ResultDocument(BaseModel):
    document_id: str
    title: str
    short_title: str
    year: int | None
    document_type: DocumentType


class SearchResult(BaseModel):
    rank: int = Field(description="1 = most similar")
    chunk_id: str = Field(examples=["NIDA-PDAT-c0007"])
    document: ResultDocument
    section_path: list[str]
    page_start: int = Field(description="1-based PDF page")
    page_end: int
    page_label_start: str | None
    page_label_end: str | None
    location: str
    text: str = Field(description="Full chunk text exactly as stored")
    matched_passage: str = Field(description="The window of the chunk that matched best (a substring of `text`)")
    matched_char_start: int
    matched_char_end: int
    distance: float = Field(description="Cosine distance 1 - similarity; 0 = same direction")
    similarity: float = Field(
        description="Cosine similarity of query and best window. Not a probability of correctness: "
                    "a high value does not make the passage right, and a low value does not make it wrong.")
    content_hash: str
    token_count: int


class RetrievalSettingsOut(BaseModel):
    """Everything that determined this result, so it can be reproduced and compared."""

    embedding_model: str
    embedding_model_version: str
    embedding_dimension: int
    metric: Literal["cosine"] = "cosine"
    search_type: Literal["exact"] = "exact"
    scoring: str = "chunk score = best-matching window (minimum cosine distance)"
    top_k: int
    max_top_k: int
    min_similarity: float | None
    filters: dict
    tie_break: str = "distance ascending, then chunk_id ascending"
    deterministic: bool = True


class SearchResponse(BaseModel):
    query: str
    settings: RetrievalSettingsOut
    results: list[SearchResult]
    result_count: int
    candidates_considered: int | None = Field(description="Chunks eligible after filters (None when not counted)")
    note: str = ("Similarity scores rank passages by closeness in embedding space. They are not probabilities "
                 "that a passage is correct or relevant; read the text and its source.")
    source_kind: Literal["research_documents"] = "research_documents"


class IndexStatus(BaseModel):
    ready: bool
    embedding_model: str | None
    embedding_model_version: str | None
    dimension: int | None
    chunks: int
    embedded_chunks: int
    windows: int
    stale_chunks: int
    failed_chunks: int
    configured_model_matches: bool | None
