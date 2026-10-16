from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.schemas.health import HealthOut
from app.services import health as service

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Service and database health",
    responses={503: {"model": HealthOut, "description": "Database unavailable (status 'degraded')"}},
)
def health(request: Request, db: DbSession):
    """Liveness plus a database probe. Returns 503 with `status: degraded` when PostgreSQL is unreachable."""
    result = service.check(db, request.app.state.settings)
    if result.status != "ok":
        return JSONResponse(status_code=503, content=result.model_dump())
    return result
