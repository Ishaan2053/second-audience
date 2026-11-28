"""The Flagsmith audit-log webhook route: signature, size cap, malformed bodies, trimming."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastmcp.utilities.tests import ASGIServer, asgi_server
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, create_engine, select

from second_audience.booking.audit_hook import (
    MAX_BODY_BYTES,
    SIGNATURE_HEADER,
    WEBHOOK_PATH,
    sign,
    signature_ok,
    trim,
)
from second_audience.booking.server import build_server
from second_audience.booking.sessions import SessionManager
from second_audience.config import Settings
from second_audience.core.experiment import NullExperimentClient
from second_audience.core.ledger import FLAGSMITH_AUDIT, Ledger, audit_log

SECRET = "a-webhook-secret-of-some-length"

EVENT: dict[str, Any] = {
    "created_date": "2026-11-28T12:00:00.123456Z",
    "log": "Flag state / Remote Config value updated for feature: field_enabled",
    "author": {"id": 7, "email": "dev@example.com", "first_name": "Dev", "last_name": "Eloper"},
    "environment": {"id": 120771, "name": "Development", "api_key": "JDfx"},
    "project": {"id": 56456, "name": "second-audience", "organisation": 42163},
    "related_object_id": 99,
    "related_object_uuid": None,
    "related_object_type": "FEATURE_STATE",
}


@asynccontextmanager
async def serve(
    tmp_path: Path, token: str | None = SECRET, with_store: bool = True
) -> AsyncIterator[tuple[ASGIServer, Engine]]:
    """Serve the app in-process with the webhook wired to a SQLite ledger."""
    engine = create_engine(f"sqlite:///{tmp_path / 'hook.db'}")
    ledger = Ledger(engine)
    manager = SessionManager(ledger, NullExperimentClient(), max_tool_calls=12, idle_seconds=600)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sa_env="test",
        sa_webhook_token=SecretStr(token) if token is not None else None,
    )

    def store(detail: str) -> None:
        ledger.audit(FLAGSMITH_AUDIT, None, detail, None)

    server = build_server(manager, settings, audit_store=store if with_store else None)
    async with asgi_server(server) as running:
        yield running, engine
    engine.dispose()


def rows(engine: Engine) -> list[Any]:
    """Every audit_log row."""
    with engine.connect() as conn:
        return list(conn.execute(select(audit_log).order_by(audit_log.c.id)))


async def post(server: ASGIServer, body: bytes, signature: str | None) -> Any:
    """POST raw bytes to the webhook with an optional signature header."""
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers[SIGNATURE_HEADER] = signature
    async with server.http_client() as http:
        return await http.post(WEBHOOK_PATH, content=body, headers=headers)


async def test_valid_signed_event_is_stored_trimmed(tmp_path: Path) -> None:
    body = json.dumps(EVENT).encode()
    async with serve(tmp_path) as (server, engine):
        response = await post(server, body, sign(SECRET, body))
        stored = rows(engine)
    assert response.status_code == 200
    assert response.json() == {"status": "stored"}
    assert len(stored) == 1
    row = stored[0]
    assert (row.action, row.experiment_slug, row.approved_by) == (FLAGSMITH_AUDIT, None, None)
    detail = json.loads(row.detail)
    assert detail == {
        "created_date": EVENT["created_date"],
        "log": EVENT["log"],
        "related_object_type": "FEATURE_STATE",
        "related_object_id": 99,
        "related_object_uuid": None,
        "project": {"id": 56456, "name": "second-audience"},
        "environment": {"id": 120771, "name": "Development"},
        "author_id": 7,
    }
    assert "dev@example.com" not in row.detail
    assert "JDfx" not in row.detail


@pytest.mark.parametrize("signature", [None, "", "0" * 64, sign("another-secret-entirely", b"x")])
async def test_missing_or_wrong_signature_is_rejected(
    tmp_path: Path, signature: str | None
) -> None:
    body = json.dumps(EVENT).encode()
    async with serve(tmp_path) as (server, engine):
        response = await post(server, body, signature)
        stored = rows(engine)
    assert response.status_code == 401
    assert stored == []


async def test_signature_of_a_different_body_is_rejected(tmp_path: Path) -> None:
    body = json.dumps(EVENT).encode()
    tampered = json.dumps({**EVENT, "log": "something else"}).encode()
    async with serve(tmp_path) as (server, engine):
        response = await post(server, tampered, sign(SECRET, body))
        stored = rows(engine)
    assert response.status_code == 401
    assert stored == []


async def test_oversized_body_is_rejected_before_verification(tmp_path: Path) -> None:
    body = json.dumps({**EVENT, "log": "x" * MAX_BODY_BYTES}).encode()
    async with serve(tmp_path) as (server, engine):
        response = await post(server, body, sign(SECRET, body))
        stored = rows(engine)
    assert response.status_code == 413
    assert stored == []


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
async def test_malformed_or_non_object_json_is_rejected(tmp_path: Path, body: bytes) -> None:
    async with serve(tmp_path) as (server, engine):
        response = await post(server, body, sign(SECRET, body))
        stored = rows(engine)
    assert response.status_code == 400
    assert stored == []


@pytest.mark.parametrize(("token", "with_store"), [(None, True), (SECRET, False)])
async def test_route_is_absent_without_a_token_or_a_store(
    tmp_path: Path, token: str | None, with_store: bool
) -> None:
    body = json.dumps(EVENT).encode()
    async with serve(tmp_path, token, with_store) as (server, engine):
        response = await post(server, body, sign(SECRET, body))
        stored = rows(engine)
    assert response.status_code == 404
    assert stored == []


def test_trim_bounds_text_and_drops_wrong_types() -> None:
    record = trim(
        {
            "created_date": 12,
            "log": "y" * 5000,
            "related_object_id": True,
            "related_object_type": ["FEATURE"],
            "project": "not an object",
            "environment": None,
            "author": {"id": "7"},
            "extra": "dropped",
        }
    )
    assert record["created_date"] is None
    assert record["log"] == "y" * 300
    assert record["related_object_id"] is None
    assert record["related_object_type"] is None
    assert record["project"] is None
    assert record["environment"] is None
    assert record["author_id"] is None
    assert "extra" not in record


def test_signature_matches_flagsmiths_documented_scheme() -> None:
    # hmac.new(key=secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
    assert sign("key", b"The quick brown fox jumps over the lazy dog") == (
        "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    )
    assert signature_ok("key", b"body", f"  {sign('key', b'body')}\n")
    assert not signature_ok("key", b"body", None)


def test_webhook_token_must_be_long_enough() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sa_webhook_token=SecretStr("short"))  # type: ignore[call-arg]
