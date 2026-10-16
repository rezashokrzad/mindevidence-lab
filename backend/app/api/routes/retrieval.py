"""Semantic retrieval over the research corpus (no LLM involved)."""
from fastapi import APIRouter, Request

from app.api.deps import CsrfPrincipal, CurrentPrincipal, DbSession
from app.schemas.common import error_responses
from app.schemas.retrieval import IndexStatus, SearchRequest, SearchResponse
from app.services import retrieval as service

router = APIRouter(prefix="/retrieval", tags=["retrieval"], responses=error_responses(401, 422, 500, 503))


@router.post("/search", response_model=SearchResponse, summary="Semantic search over research chunks",
             responses={**error_responses(403), 409: error_responses(409)[409]},
             description=(
                 "Embeds the query and returns the most similar chunks by **cosine similarity** (exact search over "
                 "pgvector), each with its full text, document, section, pages, chunk id, distance and similarity, "
                 "plus the exact retrieval settings used.\n\n"
                 "**Similarity is not correctness.** Scores rank passages by closeness in embedding space; they are "
                 "not probabilities. Always read the returned text and its source.\n\n"
                 "Needs sign-in and the `X-CSRF-Token` header (POST). 503 `retrieval_unavailable` if embeddings "
                 "have not been built; 409 `embedding_model_mismatch` if the index was built by a different model."))
def search(request: Request, db: DbSession, principal: CsrfPrincipal, body: SearchRequest) -> SearchResponse:
    return service.run_search(db, request.app.state.embedder, request.app.state.retrieval_settings, body)


@router.get("/status", response_model=IndexStatus, summary="Embedding index status")
def status(request: Request, db: DbSession, principal: CurrentPrincipal) -> IndexStatus:
    return service.index_status(db, request.app.state.embedder)
