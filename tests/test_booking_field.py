"""Field hardening over in-process HTTP: rate limit, kill switch, cap, IP privacy, `/`, `/healthz`.

Every in-process request comes from the loopback address, so all field sessions here share
one client IP unless a test sets `X-Forwarded-For` behind a trusted proxy.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.utilities.tests import ASGIServer, asgi_server
from mcp.types import Implementation
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, func, select

from second_audience.booking.health import Probe
from second_audience.booking.limits import FIELD_CLOSED, RATE_LIMITED, SERVER_BUSY
from second_audience.booking.server import build_server
from second_audience.booking.sessions import SessionManager
from second_audience.config import Settings
from second_audience.core.experiment import NullExperimentClient
from second_audience.core.ledger import Ledger, sessions

LAB_KEY = "test-lab-key"
LAB_HEADERS = {"X-SA-Lab-Key": LAB_KEY, "X-SA-Agent-Label": "lab-model"}
CLIENT_INFO = Implementation(name="field-agent", version="1.0")


@asynccontextmanager
async def serve(
    tmp_path: Path, probes: dict[str, Probe | None] | None = None, **overrides: Any
) -> AsyncIterator[tuple[ASGIServer, Engine]]:
    """Serve the app in-process with the given settings overrides."""
    engine = create_engine(f"sqlite:///{tmp_path / 'field.db'}")
    manager = SessionManager(
        Ledger(engine), NullExperimentClient(), max_tool_calls=12, idle_seconds=600
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sa_env="test",
        sa_lab_key=SecretStr(LAB_KEY),
        **overrides,
    )
    async with asgi_server(build_server(manager, settings, probes)) as server:
        yield server, engine
    engine.dispose()


async def begin(server: ASGIServer, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Call begin_task in a fresh MCP connection and return its payload."""
    async with server.client(headers=headers, client_info=CLIENT_INFO) as client:
        return await _begin(client)


async def _begin(client: Client) -> dict[str, Any]:
    result = await client.call_tool("begin_task", {"scenario_id": "s01"})
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def session_count(engine: Engine) -> int:
    """Sessions recorded in the ledger."""
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(sessions)).scalar_one())


async def test_eleventh_field_session_from_one_ip_is_refused_but_lab_is_not(
    tmp_path: Path,
) -> None:
    async with serve(tmp_path) as (server, engine):
        async with server.client(client_info=CLIENT_INFO) as client:
            started = [await _begin(client) for _ in range(10)]
            refused = await _begin(client)
        lab = await begin(server, LAB_HEADERS)
        assert all("session_id" in payload for payload in started)
        assert refused["error"]["code"] == RATE_LIMITED
        assert refused["error"]["retry_after_seconds"] > 3500
        assert "session_id" not in refused
        assert "session_id" in lab
        # The refused begin_task created no session.
        assert session_count(engine) == 11


async def test_kill_switch_refuses_field_sessions_and_keeps_lab(tmp_path: Path) -> None:
    async with serve(tmp_path, sa_field_enabled=False) as (server, engine):
        refused = await begin(server)
        lab = await begin(server, LAB_HEADERS)
        assert refused["error"]["code"] == FIELD_CLOSED
        assert "session_id" in lab
        assert session_count(engine) == 1


async def test_concurrency_cap_counts_open_sessions_only(tmp_path: Path) -> None:
    async with serve(tmp_path, sa_max_concurrent_sessions=2) as (server, _):
        async with server.client(client_info=CLIENT_INFO) as client:
            first = await _begin(client)
            await begin(server, LAB_HEADERS)  # lab sessions count towards the cap
            busy = await _begin(client)
            assert busy["error"]["code"] == SERVER_BUSY
            assert busy["error"]["retry_after_seconds"] == 60
            # Lab sessions are exempt from the cap itself.
            assert "session_id" in await begin(server, LAB_HEADERS)
            await client.call_tool(
                "finish_task", {"session_id": first["session_id"], "summary": "done"}
            )
        # Two lab sessions remain open, so field is still refused until one closes.
        assert (await begin(server))["error"]["code"] == SERVER_BUSY


async def test_trusted_proxy_limits_by_forwarded_ip_and_stores_no_raw_ip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    first_ip, second_ip = "203.0.113.7", "198.51.100.23"
    async with serve(tmp_path, sa_trust_proxy=True, sa_field_sessions_per_ip_per_hour=1) as (
        server,
        engine,
    ):
        assert "session_id" in await begin(server, {"X-Forwarded-For": f"{first_ip}, 10.0.0.1"})
        assert "session_id" in await begin(server, {"X-Forwarded-For": second_ip})
        refused = await begin(server, {"X-Forwarded-For": first_ip})
        assert refused["error"]["code"] == RATE_LIMITED
        with engine.connect() as conn:
            hashes = conn.execute(select(sessions.c.client_ip_hash)).scalars().all()
    assert len(set(hashes)) == 2
    assert all(value is not None and len(value) == 32 for value in hashes)
    database = (tmp_path / "field.db").read_bytes()
    for ip in (first_ip, second_ip, "127.0.0.1"):
        assert ip.encode() not in database
    # Not the loopback peer here: the in-process client logs its own URL, http://127.0.0.1/mcp.
    for ip in (first_ip, second_ip):
        assert ip not in caplog.text


async def test_untrusted_forwarded_for_is_ignored(tmp_path: Path) -> None:
    async with serve(tmp_path, sa_field_sessions_per_ip_per_hour=1) as (server, _):
        assert "session_id" in await begin(server, {"X-Forwarded-For": "203.0.113.7"})
        refused = await begin(server, {"X-Forwarded-For": "198.51.100.23"})
        assert refused["error"]["code"] == RATE_LIMITED


async def test_healthz_reports_commit_sessions_and_checks_without_auth(tmp_path: Path) -> None:
    calls: list[str] = []

    def database() -> bool:
        calls.append("database")
        return True

    probes: dict[str, Probe | None] = {"database": database, "flagsmith": None}
    async with serve(tmp_path, probes, sa_git_commit="abc1234") as (server, _):
        await begin(server)
        async with server.http_client() as http:
            first = await http.get("/healthz")
            second = await http.get("/healthz")
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "ok"
    assert body["commit"] == "abc1234"
    assert body["active_sessions"] == 1
    assert body["field_enabled"] is True
    assert body["checks"] == {"database": True, "flagsmith": None}
    assert second.json()["checked_at"] == body["checked_at"]
    assert calls == ["database"]  # cached, not re-checked per request


async def test_healthz_is_degraded_when_a_dependency_is_down(tmp_path: Path) -> None:
    async with (
        serve(tmp_path, {"database": lambda: False}) as (server, _),
        server.http_client() as http,
    ):
        body = (await http.get("/healthz")).json()
    assert body["status"] == "degraded"
    assert body["commit"] is None


async def test_landing_page_explains_logging_and_how_to_connect(tmp_path: Path) -> None:
    async with serve(tmp_path) as (server, _), server.http_client() as http:
        response = await http.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    for phrase in (
        "research sandbox",
        "fictional",
        "salted, one-way hash of your IP address",
        "client name",
        "A/B test",
        "/mcp",
        "Use the second-audience tools. Call begin_task, complete the task it gives you, "
        "then call finish_task.",
        "https://github.com/Ishaan2053/second-audience",
    ):
        assert phrase in page
    # No external assets.
    assert "src=" not in page
    assert "<link" not in page
