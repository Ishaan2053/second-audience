"""`sa doctor`: every precondition for running an experiment, checked and reported at once."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import ValidationError
from sqlalchemy import text

from second_audience.config import Settings
from second_audience.core.ledger import make_engine
from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError
from second_audience.operator.spec import ExperimentSpec, load_spec

SERVER_TIMEOUT_SECONDS = 15
BOOKING_TOOL = "begin_task"


class Status(StrEnum):
    """Outcome of one check. `skip` means "not applicable here" and does not fail."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    """One row of the doctor table."""

    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class Report:
    """All checks, plus the objects later steps need when they passed."""

    checks: list[Check]
    spec: ExperimentSpec | None
    environment_key: str | None

    @property
    def ok(self) -> bool:
        """True when no check failed."""
        return all(check.status is not Status.FAIL for check in self.checks)


async def _list_tools(url: str) -> list[str]:
    """Open an MCP session to the server and list its tools."""
    async with Client(StreamableHttpTransport(url), timeout=SERVER_TIMEOUT_SECONDS) as client:
        return [tool.name for tool in await client.list_tools()]


def probe_server(url: str) -> None:
    """Raise unless the booking MCP server answers at `url`.

    Listing tools, not `ping`: FastMCP's server answers `ping` with "Method not found", and
    the tool list also proves it is the booking server.
    """
    tools = asyncio.run(_list_tools(url))
    if BOOKING_TOOL not in tools:
        raise LookupError(f"no {BOOKING_TOOL} tool at {url}; tools: {tools}")


def check_database(settings: Settings) -> str:
    """Raise unless the ledger database accepts a connection; returns the backend name."""
    engine = make_engine(settings)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    return engine.url.get_backend_name()


def run_checks(
    load_settings: Callable[[], Settings],
    spec_path: Path,
    make_admin: Callable[[Settings], FlagsmithAdmin],
    *,
    database: Callable[[Settings], str] = check_database,
    server: Callable[[str], None] = probe_server,
) -> Report:
    """Run every check; a failure never stops the later checks that do not depend on it."""
    checks: list[Check] = []

    def record(name: str, status: Status, detail: str) -> None:
        checks.append(Check(name, status, detail))

    try:
        settings = load_settings()
    except ValidationError as error:
        record("settings", Status.FAIL, f"{error.error_count()} invalid value(s): {error}")
        return Report(checks, None, None)
    record("settings", Status.PASS, f"SA_ENV={settings.sa_env}")

    spec: ExperimentSpec | None = None
    try:
        spec = load_spec(spec_path)
    except (OSError, ValueError) as error:
        record("spec", Status.FAIL, f"{spec_path}: {error}")
    else:
        if spec.flag == settings.sa_flag_name:
            record("spec", Status.PASS, f"{spec.slug}: flag {spec.flag} in {spec.environment}")
        else:
            record(
                "spec",
                Status.FAIL,
                f"spec flag {spec.flag!r} differs from SA_FLAG_NAME={settings.sa_flag_name!r}",
            )

    environment_key = _flagsmith_checks(make_admin(settings), spec, settings, record)

    try:
        backend = database(settings)
    except Exception as error:
        # The type only: a driver message can echo the connection URL, password included.
        record("database", Status.FAIL, f"cannot connect ({type(error).__name__})")
    else:
        record("database", Status.PASS, f"{backend} accepts connections")

    if settings.gemini_api_key is None:
        record("model key", Status.FAIL, "GEMINI_API_KEY is not set")
    else:
        record("model key", Status.PASS, "GEMINI_API_KEY is set")

    if settings.sa_server_url is None:
        record("server", Status.FAIL, "SA_SERVER_URL is not set")
    else:
        try:
            server(settings.sa_server_url)
        except Exception as error:
            record("server", Status.FAIL, f"{settings.sa_server_url}: {type(error).__name__}")
        else:
            record("server", Status.PASS, f"booking tools listed at {settings.sa_server_url}")

    return Report(checks, spec, environment_key)


def _flagsmith_checks(
    admin: FlagsmithAdmin,
    spec: ExperimentSpec | None,
    settings: Settings,
    record: Callable[[str, Status, str], None],
) -> str | None:
    """CLI, login, environment, warehouse and server-key checks; returns the env key."""
    try:
        executable = admin.executable
    except FlagsmithAdminError as error:
        record("flagsmith cli", Status.FAIL, error.stderr)
        return None
    record("flagsmith cli", Status.PASS, executable)

    try:
        record("flagsmith login", Status.PASS, admin.whoami())
    except FlagsmithAdminError as error:
        record("flagsmith login", Status.FAIL, error.stderr)
        return None

    if spec is None:
        record("environment", Status.SKIP, "no valid spec")
        return None
    try:
        environment_key = admin.environment_key(spec.environment)
    except FlagsmithAdminError as error:
        record("environment", Status.FAIL, f"project {admin.project}: {error.stderr}")
        return None
    record("environment", Status.PASS, f"{spec.environment} in project {admin.project}")

    try:
        connected = admin.warehouse_connected(environment_key)
    except FlagsmithAdminError as error:
        record("warehouse", Status.FAIL, error.stderr)
    else:
        if connected:
            record("warehouse", Status.PASS, f"connected for {spec.environment}")
        else:
            record("warehouse", Status.FAIL, f"no warehouse connection for {spec.environment}")

    # The server's SDK key decides which environment sessions land in. A key for the wrong
    # environment silently sends traffic elsewhere (docs/checkpoint-a.md), so compare it.
    if settings.flagsmith_environment_key is None:
        record("server sdk key", Status.SKIP, "FLAGSMITH_ENVIRONMENT_KEY is not set here")
    else:
        try:
            keys = admin.server_keys(environment_key)
        except FlagsmithAdminError as error:
            record("server sdk key", Status.FAIL, error.stderr)
        else:
            if settings.flagsmith_environment_key.get_secret_value() in keys:
                record("server sdk key", Status.PASS, f"belongs to {spec.environment}")
            else:
                record(
                    "server sdk key",
                    Status.FAIL,
                    f"FLAGSMITH_ENVIRONMENT_KEY is not a server key of {spec.environment}",
                )
    return environment_key
