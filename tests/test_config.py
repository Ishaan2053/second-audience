"""Tests for `second_audience.config.Settings`."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from second_audience.config import Settings, get_settings

ALL_VARS = (
    "SA_ENV",
    "FLAGSMITH_ENVIRONMENT_KEY",
    "FLAGSMITH_API_URL",
    "FLAGSMITH_REQUEST_TIMEOUT_SECONDS",
    "SA_FLAG_NAME",
    "SA_DATABASE_URL",
    "GEMINI_API_KEY",
    "SA_LAB_MODELS",
    "SA_LAB_KEY",
    "SA_SERVER_URL",
    "SA_LAB_REQUESTS_PER_MINUTE",
    "SA_LAB_REQUESTS_PER_DAY",
    "SA_LAB_CONCURRENCY",
    "SA_MAX_TOOL_CALLS",
    "SA_SESSION_IDLE_SECONDS",
    "SA_FIELD_SESSIONS_PER_IP_PER_HOUR",
    "SA_FIELD_ENABLED",
    "SA_MAX_CONCURRENT_SESSIONS",
    "SA_TRUST_PROXY",
    "SA_GIT_COMMIT",
)

PROD_VARS = {
    "SA_ENV": "prod",
    "FLAGSMITH_ENVIRONMENT_KEY": "ser.flagsmith-secret",
    "SA_DATABASE_URL": "postgresql://user:db-secret@host/db",
    "SA_LAB_KEY": "lab-secret",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's shell environment."""
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def load(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build Settings from the given env vars only, ignoring any local `.env` file."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch)
    assert settings.sa_env == "dev"
    assert settings.sa_flag_name == "error_style"
    assert settings.flagsmith_request_timeout_seconds == 2
    assert settings.sa_database_url.get_secret_value() == "sqlite:///./sa.db"
    assert settings.sa_max_tool_calls == 12
    assert settings.sa_session_idle_seconds == 600
    assert settings.sa_field_sessions_per_ip_per_hour == 10
    assert settings.sa_field_enabled is True
    assert settings.sa_max_concurrent_sessions == 50
    assert settings.sa_trust_proxy is False
    assert settings.sa_git_commit is None
    assert settings.sa_lab_models == []
    assert settings.flagsmith_environment_key is None
    assert settings.gemini_api_key is None
    assert settings.flagsmith_api_url is None
    assert settings.sa_server_url is None
    assert settings.sa_lab_requests_per_minute == 8
    assert settings.sa_lab_requests_per_day == 400
    assert settings.sa_lab_concurrency == 1


@pytest.mark.parametrize("value", ["0", "4"])
def test_lab_concurrency_is_one_to_three(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    with pytest.raises(ValidationError):
        load(monkeypatch, SA_LAB_CONCURRENCY=value)


def test_prod_with_all_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, **PROD_VARS)
    assert settings.sa_env == "prod"
    assert settings.sa_lab_key == SecretStr("lab-secret")


@pytest.mark.parametrize("missing", ["FLAGSMITH_ENVIRONMENT_KEY", "SA_DATABASE_URL", "SA_LAB_KEY"])
def test_prod_missing_required_variable_names_it(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    env = {k: v for k, v in PROD_VARS.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        load(monkeypatch, **env)


def test_validation_error_does_not_echo_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {k: v for k, v in PROD_VARS.items() if k != "SA_LAB_KEY"}
    with pytest.raises(ValidationError) as excinfo:
        load(monkeypatch, **env)
    assert "ser.flagsmith-secret" not in str(excinfo.value)
    assert "db-secret" not in str(excinfo.value)


def test_prod_empty_required_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="SA_LAB_KEY"):
        load(monkeypatch, **{**PROD_VARS, "SA_LAB_KEY": ""})


def test_dev_does_not_require_prod_values(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load(monkeypatch, SA_ENV="dev").sa_lab_key is None


def test_invalid_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        load(monkeypatch, SA_ENV="staging")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gemini-a", ["gemini-a"]),
        ("gemini-a,gemini-b", ["gemini-a", "gemini-b"]),
        (" gemini-a , gemini-b ,", ["gemini-a", "gemini-b"]),
        ("", []),
    ],
)
def test_lab_models_parsing(monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]) -> None:
    assert load(monkeypatch, SA_LAB_MODELS=raw).sa_lab_models == expected


def test_secrets_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, **PROD_VARS, GEMINI_API_KEY="gemini-secret")
    rendered = f"{settings!r} {settings!s} {settings.model_dump()}"
    for secret in ("ser.flagsmith-secret", "db-secret", "lab-secret", "gemini-secret"):
        assert secret not in rendered


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
