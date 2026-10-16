"""Answers: run the LangGraph workflow, inspect its trace, and rate an answer. Sign-in and CSRF required for POSTs."""
from typing import Annotated

from fastapi import APIRouter, Path, Request, status

from app.api.deps import CsrfPrincipal, CurrentPrincipal, DbSession
from app.schemas.answers import AnswerRequest, AnswerResponse, FeedbackOut, FeedbackRequest, TraceResponse
from app.schemas.common import error_responses
from app.services import answers as service

router = APIRouter(tags=["answers"], responses=error_responses(401, 422, 500, 503))

CorrelationPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,64}$", examples=["cid-0a1b2c3d4e5f"])]


@router.post("/answers", response_model=AnswerResponse, summary="Ask a question about a synthetic patient case",
             responses={**error_responses(403, 404), 409: error_responses(409)[409]},
             description=(
                 "Runs the question through the LangGraph workflow: validate → classify → decide retrieval → retrieve → "
                 "assess evidence → generate (DeepSeek) → validate citations → finalize.\n\n"
                 "Modes: `gk` (no retrieval), `rag` (research documents only), `both` (documents + clearly separated "
                 "general knowledge), `auto` (the model may call the search tool, capped by the backend).\n\n"
                 "Educational use only: the backend refuses diagnosis, prescription and individual treatment selection, "
                 "and returns an escalation message for urgent wording, all without calling the model. "
                 "Claims are returned as structured data with citation ids; unsupported or conflicting claims are flagged. "
                 "Model failures return a clear error (502/503/504) and are never replaced by invented text."))
def ask(request: Request, db: DbSession, principal: CsrfPrincipal, body: AnswerRequest) -> AnswerResponse:
    return service.answer_question(db, request.app.state.workflow, request.app.state.audit, principal, body)


@router.get("/answers/{correlation_id}/trace", response_model=TraceResponse, summary="Execution trace of one answer",
            responses=error_responses(404),
            description=("Audit events of one question in order: submission, workflow nodes with decision reasons and "
                         "latency, retrieval settings and chunk ids, model-call metadata, citation validation. "
                         "Only the asking user (or an instructor) can read it; other ids look like missing ones."))
def trace(request: Request, db: DbSession, principal: CurrentPrincipal, correlation_id: CorrelationPath) -> TraceResponse:
    return service.get_trace(db, principal, correlation_id)


@router.post("/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED,
             summary="Rate an answer you received", responses=error_responses(403, 404))
def feedback(request: Request, db: DbSession, principal: CsrfPrincipal, body: FeedbackRequest) -> FeedbackOut:
    return service.submit_feedback(db, request.app.state.audit, principal, body)
