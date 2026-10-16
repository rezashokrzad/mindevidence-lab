"""LLM settings from environment variables (see backend/.env.example). The key never leaves the backend."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True, hide_input_in_errors=True)

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    # Model identifier as named in DeepSeek's API reference ("deepseek-flash" or "deepseek-v4-pro").
    deepseek_model: str = "deepseek-flash"
    # "disabled" = plain answers (faster, cheaper, needed for forced tool use). "enabled" turns on the model's
    # reasoning mode; its reasoning_content is NEVER read, stored, or shown by this application.
    deepseek_thinking: Literal["disabled", "enabled"] = "disabled"
    deepseek_temperature: float = Field(default=0.2, ge=0, le=2)
    deepseek_max_output_tokens: int = Field(default=1800, ge=64, le=32000)
    deepseek_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    deepseek_read_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    deepseek_max_retries: int = Field(default=2, ge=0, le=6)
    deepseek_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=30)
    # Hard limits applied by the backend, regardless of what the model asks for.
    llm_max_tool_calls: int = Field(default=2, ge=0, le=5)
    llm_max_repair_attempts: int = Field(default=1, ge=0, le=2)

    @field_validator("deepseek_base_url")
    @classmethod
    def _https(cls, v: str) -> str:
        parts = urlsplit(v)
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("DEEPSEEK_BASE_URL must use https:// (http is allowed only for localhost test servers)")
        return v.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value())
