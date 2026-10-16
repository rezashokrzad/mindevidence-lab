"""Exception handlers: every error leaves the API in the same envelope.

    {"error": {"code": "...", "message": "...", "details": [...], "request_id": "..."}}
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import AppError

log = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "bad_request", 401: "authentication_required", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 406: "not_acceptable", 413: "payload_too_large", 415: "unsupported_media_type",
}


def error_json(request: Request, status: int, code: str, message: str, details: list | None = None,
               headers: dict | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or [],
                      "request_id": getattr(request.state, "request_id", None)}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error_json(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"loc": [str(p) if not isinstance(p, int) else p for p in err.get("loc", [])],
             "message": err.get("msg", "invalid value"),
             "type": err.get("type")}
            for err in exc.errors()
        ]
        return error_json(request, 422, "validation_error", "The request is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404 and message == "Not Found":
            message = "No such endpoint."
        return error_json(request, exc.status_code, code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        if isinstance(exc, OperationalError) or (isinstance(exc, DBAPIError) and exc.connection_invalidated):
            log.error("database unavailable: %s", type(exc).__name__)
            return error_json(request, 503, "database_unavailable", "The database is unavailable. Try again shortly.")
        log.exception("database error")
        return error_json(request, 500, "internal_error", "An unexpected error occurred.")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return error_json(request, 500, "internal_error", "An unexpected error occurred.")
