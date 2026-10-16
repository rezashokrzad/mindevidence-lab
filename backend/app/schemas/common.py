"""Shared API schemas: pagination envelope, error envelope, partial dates, ids."""
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

T = TypeVar("T")

PATIENT_ID_PATTERN = r"^P-[0-9]{3}$"
PatientId = Annotated[str, StringConstraints(pattern=PATIENT_ID_PATTERN), Field(examples=["P-001"])]

Mode = Literal["gk", "rag", "both", "auto"]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(ApiModel, Generic[T]):
    """Offset pagination envelope used by every list endpoint."""

    items: list[T]
    total: int = Field(description="Total matching items, ignoring limit/offset")
    limit: int
    offset: int


class PartialDate(ApiModel):
    """A date known to day, month, or year precision, or not recorded at all."""

    value: str | None = Field(description="'YYYY', 'YYYY-MM', 'YYYY-MM-DD', or null when not recorded",
                              examples=["2024-03"])
    precision: Literal["day", "month", "year", "unknown"]


class ErrorItem(BaseModel):
    loc: list[str | int] | None = Field(default=None, description="Location of the invalid input, if any")
    message: str
    type: str | None = None


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code", examples=["patient_not_found"])
    message: str = Field(description="Human-readable explanation")
    details: list[ErrorItem] = Field(default_factory=list)
    request_id: str | None = Field(default=None, description="Matches the X-Request-ID response header")


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape."""

    error: ErrorBody


def error_responses(*statuses: int) -> dict[int, dict]:
    descriptions = {
        400: "Bad request",
        401: "Authentication required (sign in with Google)",
        403: "Forbidden: CSRF check failed (`csrf_failed`) or role not allowed (`forbidden`)",
        409: "Conflict",
        404: "Not found",
        429: "Rate limited (local limit or the upstream service)",
        502: "Upstream service failed (language model or Gmail)",
        422: "Invalid input",
        500: "Unexpected server error",
        503: "Database unavailable",
    }
    return {s: {"model": ErrorResponse, "description": descriptions[s]} for s in statuses}
