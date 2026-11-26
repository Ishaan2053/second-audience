"""Application settings, loaded from the environment and an optional `.env` file."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]

_REQUIRED_IN_PROD = ("flagsmith_environment_key", "sa_database_url", "sa_lab_key")


class Settings(BaseSettings):
    """Every configuration value the project reads. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,  # validation errors must not echo raw secrets
        frozen=True,
    )

    sa_env: Environment = "dev"

    flagsmith_environment_key: SecretStr | None = None
    flagsmith_api_url: str | None = None
    flagsmith_request_timeout_seconds: int = Field(default=2, gt=0)
    sa_flag_name: str = "error_style"

    sa_database_url: SecretStr = SecretStr("sqlite:///./sa.db")

    gemini_api_key: SecretStr | None = None
    sa_lab_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    sa_lab_key: SecretStr | None = None
    sa_server_url: str | None = None
    sa_lab_requests_per_minute: int = Field(default=8, gt=0)
    sa_lab_requests_per_day: int = Field(default=400, gt=0)
    sa_lab_concurrency: int = Field(default=1, ge=1, le=3)

    sa_max_tool_calls: int = Field(default=12, gt=0)
    sa_session_idle_seconds: int = Field(default=600, gt=0)
    sa_field_sessions_per_ip_per_hour: int = Field(default=10, gt=0)

    @field_validator("sa_lab_models", mode="before")
    @classmethod
    def _split_models(cls, value: object) -> object:
        """Parse a comma-separated string of model IDs into a list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _require_prod_values(self) -> Self:
        """In prod, fail fast unless every required variable was set explicitly."""
        if self.sa_env != "prod":
            return self
        missing = [
            name.upper()
            for name in _REQUIRED_IN_PROD
            if name not in self.model_fields_set or getattr(self, name) is None
        ]
        if missing:
            raise ValueError(
                f"SA_ENV=prod requires these variables to be set: {', '.join(missing)}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once."""
    return Settings()
