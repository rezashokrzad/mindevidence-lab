"""Pydantic models for answers: what the model must return, and what the API returns.

Basis of a claim (the model declares it; the backend verifies it):
  evidence           supported by retrieved research chunks (must cite E-ids)
  general_knowledge  background knowledge NOT taken from the research documents (never cited)
  patient_record     a fact from the synthetic case file (must cite F-ids)
  unsupported        the model says it cannot support the statement from what it was given
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Mode = Literal["gk", "rag", "both", "auto"]
Basis = Literal["evidence", "general_knowledge", "patient_record", "unsupported"]

CitationId = Annotated[str, StringConstraints(pattern=r"^[EF][0-9]{1,3}$")]
ClaimText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=900)]


# ---------------------------------------------------------------- what the model returns

class ModelClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: ClaimText
    basis: Basis
    citations: list[CitationId] = Field(default_factory=list, max_length=8)
    conflicts_with: list[CitationId] = Field(
        default_factory=list, max_length=8,
        description="Evidence ids whose findings disagree with this claim's evidence")


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claims: list[ModelClaim] = Field(min_length=1, max_length=25)
    limitations: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]] = Field(
        default_factory=list, max_length=8)


# ---------------------------------------------------------------- inputs the model is shown

class EvidenceItem(BaseModel):
    """A retrieved research chunk with a stable citation id for this answer (E1, E2, ...)."""

    citation_id: str
    chunk_id: str
    document_id: str
    document_title: str
    document_short_title: str
    section: str
    page_start: int
    page_end: int
    page_label: str | None = None
    location: str
    text: str
    matched_passage: str
    similarity: float
    distance: float
    content_hash: str


class PatientFact(BaseModel):
    """One statement from the synthetic case file with a stable id for this answer (F1, F2, ...)."""

    citation_id: str
    label: str
    value: str
    field: str


# ---------------------------------------------------------------- what the API returns

class ClaimOut(BaseModel):
    index: int
    text: str
    basis: Basis
    citations: list[str]
    conflicts_with: list[str]
    flags: list[str] = Field(
        default_factory=list,
        description="Backend findings about this claim, e.g. 'downgraded_missing_citation', 'invalid_citation_removed'")


class CitationOut(BaseModel):
    """Structured citation: lets the UI open the cited chunk or case fact."""

    citation_id: str
    kind: Literal["research_chunk", "patient_fact"]
    supports_claims: list[int]
    # research chunk fields
    chunk_id: str | None = None
    document_id: str | None = None
    document_title: str | None = None
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    location: str | None = None
    similarity: float | None = None
    content_hash: str | None = None
    excerpt: str | None = None
    # patient fact fields
    fact_label: str | None = None
    fact_value: str | None = None


class AnswerIssue(BaseModel):
    code: str
    message: str
    claim_index: int | None = None


class SafetyOut(BaseModel):
    urgent: bool = False
    refused: Literal["diagnosis", "prescription", "treatment_selection"] | None = None
    escalation_message: str | None = None
    clinical_disclaimer: str


class AnswerBody(BaseModel):
    claims: list[ClaimOut]
    limitations: list[str]
    citations: list[CitationOut]
    unsupported_claim_count: int
    has_conflicting_evidence: bool
