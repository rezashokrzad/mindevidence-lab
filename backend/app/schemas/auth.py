import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: Literal["student", "instructor"]


class AuthSessionOut(BaseModel):
    """Current sign-in state. Always 200; `authenticated` tells whether a valid session exists."""

    authenticated: bool
    user: UserOut | None
    csrf_token: str | None = Field(
        description="Send as X-CSRF-Token on every POST. Bound to this login session.")
    expires_at: datetime | None
    sign_in_available: bool = Field(description="False when Google credentials are not configured on the server")
