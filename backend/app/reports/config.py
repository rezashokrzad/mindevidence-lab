"""Email-report settings (environment variables). The feature is OFF unless a token-encryption key is configured."""
from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
# Identity scopes are requested only to confirm that the Google account granting Gmail access is the one signed in.
GMAIL_FLOW_SCOPES = ("openid", "email", GMAIL_SEND_SCOPE)


class ReportSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True, hide_input_in_errors=True)

    # Fernet key (urlsafe base64, 32 bytes). Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    email_token_encryption_key: SecretStr = SecretStr("")
    gmail_redirect_uri: str = "http://localhost:8000/api/reports/email/callback"
    gmail_api_base: str = "https://gmail.googleapis.com"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    report_min_interval_seconds: int = Field(default=300, ge=0, le=86400)
    report_max_per_day: int = Field(default=3, ge=1, le=50)
    report_period_days: int = Field(default=7, ge=1, le=90)
    report_http_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    report_max_retries: int = Field(default=2, ge=0, le=5)
    report_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=30)

    @field_validator("email_token_encryption_key")
    @classmethod
    def _valid_key(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw:
            try:
                Fernet(raw.encode())
            except Exception:
                raise ValueError("EMAIL_TOKEN_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes)") from None
        return v

    @property
    def key_configured(self) -> bool:
        return bool(self.email_token_encryption_key.get_secret_value())
