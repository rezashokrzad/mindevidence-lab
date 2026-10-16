from typing import Literal

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    schema_version: str | None = Field(description="Applied Alembic revision, null if unavailable")
    version: str
    environment: Literal["development", "test", "production"]
    sign_in: Literal["google", "not_configured"] = Field(
        description="'not_configured' when GOOGLE_CLIENT_ID/SECRET are missing; protected endpoints then "
                    "stay unreachable because nobody can sign in")
