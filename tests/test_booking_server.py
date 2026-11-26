"""The booking MCP server over in-process streamable HTTP, driven by an MCP client."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, get_args

import pytest
from fastmcp import Client
from fastmcp.utilities.tests import asgi_client
from mcp.types import Implementation
from pydantic import SecretStr
from sqlalchemy import create_engine

from second_audience.booking.scenarios import SCENARIOS, Scenario, get_scenario
from second_audience.booking.server import IsoDate, RoomId, ScenarioId, build_server
from second_audience.booking.sessions import SessionManager
from second_audience.booking.world import ROOMS, WEEK, format_time
from second_audience.config import Settings
from second_audience.core.experiment import NullExperimentClient
from second_audience.core.ledger import Ledger, SessionSummary
from second_audience.core.styles import ARM_FLAG_VALUES, ErrorStyle

LAB_KEY = "test-lab-key"
TOOLS = {
    "begin_task",
    "list_rooms",
    "check_availability",
    "book_room",
    "reschedule_booking",
    "cancel_booking",
    "finish_task",
}


class Harness:
    """A server bound to a ledger, plus the responses it has sent."""

    def __init__(self, ledger: Ledger, experiment: NullExperimentClient) -> None:
        self.ledger = ledger
        self.experiment = experiment
        self.responses: list[str] = []

    async def call(self, client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
        """Call a tool, keep the raw response text, and return the structured payload."""
        result = await client.call_tool(tool, arguments)
        self.responses.extend(block.text for block in result.content if hasattr(block, "text"))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def summary(self, session_id: str) -> SessionSummary:
        """The ledger summary of one session."""
        [summary] = self.ledger.session_summaries([session_id])
        return summary


@asynccontextmanager
async def serve(
    tmp_path: Path, variant: str, headers: dict[str, str] | None = None
) -> AsyncIterator[tuple[Client, Harness]]:
    """Serve the app in-process with every session in `variant`, and connect a client."""
    engine = create_engine(f"sqlite:///{tmp_path / f'{variant}.db'}")
    ledger = Ledger(engine)
    experiment = NullExperimentClient(
        style=ErrorStyle.from_flag_value(ARM_FLAG_VALUES[variant]), variant=variant
    )
    manager = SessionManager(ledger, experiment, max_tool_calls=12, idle_seconds=600)
    # Field limits are tested in test_booking_field.py; here they must not cut a run short.
    settings = Settings(
        sa_env="test", sa_lab_key=SecretStr(LAB_KEY), sa_field_sessions_per_ip_per_hour=1000
    )
    server = build_server(manager, settings)
    async with asgi_client(
        server,
        headers=headers,
        client_info=Implementation(name="test-agent", version="1.0"),
    ) as client:
        yield client, Harness(ledger, experiment)
    engine.dispose()


async def _play(client: Client, harness: Harness, scenario: Scenario) -> dict[str, Any]:
    """Begin, make the first attempt, follow corrected_call or else book the solution, finish."""
    started = await harness.call(client, "begin_task", scenario_id=scenario.id)
    session_id = started["session_id"]
    attempt = scenario.first_attempt
    error = await harness.call(
        client,
        "book_room",
        session_id=session_id,
        room_id=attempt.room_id,
        date=attempt.date.isoformat(),
        start=format_time(attempt.start),
        end=format_time(attempt.end),
        attendees=attempt.attendees,
        title=attempt.title,
    )
    if "corrected_call" in error:
        corrected = error["corrected_call"]
        booked = await harness.call(client, corrected["tool"], **corrected["arguments"])
    else:
        solution = scenario.solution
        booked = await harness.call(
            client,
            "book_room",
            session_id=session_id,
            room_id=solution.room_id,
            date=solution.date.isoformat(),
            start=format_time(solution.start),
            end=format_time(solution.end),
            attendees=solution.attendees,
            title=solution.title,
        )
    assert "booking" in booked
    finished = await harness.call(client, "finish_task", session_id=session_id, summary="done")
    return {"session_id": session_id, "error": error, "finished": finished}


def test_schema_literals_match_the_domain() -> None:
    assert set(get_args(RoomId)) == {room.room_id for room in ROOMS}
    assert set(get_args(IsoDate)) == {day.isoformat() for day in WEEK}
    assert set(get_args(ScenarioId)) == {scenario.id for scenario in SCENARIOS}


async def test_lists_the_seven_tools_with_a_strict_schema(tmp_path: Path) -> None:
    async with serve(tmp_path, "control") as (client, _):
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert set(tools) == TOOLS
    props = tools["book_room"].input_schema["properties"]
    assert props["room_id"]["enum"] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert props["date"]["enum"] == [day.isoformat() for day in WEEK]
    assert props["start"]["pattern"] == props["end"]["pattern"]
    assert (props["attendees"]["minimum"], props["attendees"]["maximum"]) == (1, 20)
    assert set(tools["book_room"].input_schema["required"]) == {
        "session_id",
        "room_id",
        "date",
        "start",
        "end",
        "attendees",
        "title",
    }


async def _schema_violation(tmp_path: Path, variant: str) -> str:
    async with serve(tmp_path, variant) as (client, harness):
        started = await harness.call(client, "begin_task", scenario_id="s01")
        result = await client.call_tool(
            "book_room",
            {
                "session_id": started["session_id"],
                "room_id": "R1",
                "date": "2027-03-15",
                "start": "10:15",
                "end": "11:00",
                "attendees": "5",
                "title": "x",
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert harness.ledger.failing_sessions(10) == []
    return " ".join(block.text for block in result.content if hasattr(block, "text"))


async def test_schema_violations_are_identical_in_both_arms(tmp_path: Path) -> None:
    control = await _schema_violation(tmp_path, "control")
    prescriptive = await _schema_violation(tmp_path, "prescriptive")
    assert control == prescriptive
    assert control


@pytest.mark.parametrize(
    ("scenario_id", "variant", "expected"),
    [
        ("s01", "control", "correct"),
        ("s01", "prescriptive", "correct"),
        ("s02", "control", "correct"),
        ("s02", "prescriptive", "incorrect"),
    ],
)
async def test_aligned_and_trap_scenarios_under_each_style(
    tmp_path: Path, scenario_id: str, variant: str, expected: str
) -> None:
    async with serve(tmp_path, variant) as (client, harness):
        played = await _play(client, harness, get_scenario(scenario_id))
        session_id = played["session_id"]

        assert played["finished"] == {
            "session_id": session_id,
            "finished": True,
            "outcome": expected,
        }
        summary = harness.summary(session_id)
        assert (summary.variant, summary.in_experiment) == (variant, True)
        assert (summary.outcome, summary.final_state) == (expected, expected)
        event = "task_completed" if expected == "correct" else "task_wrong_booking"
        # Two domain calls: the first attempt and the booking that follows it.
        assert harness.experiment.tracked == [
            (session_id, event, None),
            (session_id, "task_finished", 2),
        ]
        # The variant key never reaches the client.
        assert not any(variant in text for text in harness.responses)


async def test_error_payloads_differ_only_by_the_added_fields(tmp_path: Path) -> None:
    errors: dict[str, dict[str, Any]] = {}
    for variant in ("control", "prescriptive"):
        async with serve(tmp_path, variant) as (client, harness):
            errors[variant] = (await _play(client, harness, get_scenario("s05")))["error"]

    control, prescriptive = errors["control"], errors["prescriptive"]
    assert set(control) == {"error"}
    assert set(prescriptive) == {"error", "suggestion", "corrected_call"}
    assert prescriptive["error"] == control["error"]


@pytest.mark.parametrize(
    ("headers", "source", "label"),
    [
        (
            {"X-SA-Lab-Key": LAB_KEY, "X-SA-Agent-Label": "gemini-3.1-flash-lite"},
            "lab",
            "gemini-3.1-flash-lite",
        ),
        (
            {"X-SA-Lab-Key": "wrong", "X-SA-Agent-Label": "gemini-3.1-flash-lite"},
            "field",
            "test-agent",
        ),
        (None, "field", "test-agent"),
    ],
)
async def test_source_and_label_come_from_headers_or_client_name(
    tmp_path: Path, headers: dict[str, str] | None, source: str, label: str
) -> None:
    async with serve(tmp_path, "control", headers) as (client, harness):
        started = await harness.call(client, "begin_task")
        summary = harness.summary(started["session_id"])
    assert (summary.source, summary.agent_label) == (source, label)


async def test_responses_never_contain_the_variant(tmp_path: Path) -> None:
    async with serve(tmp_path, "prescriptive") as (client, harness):
        for scenario in SCENARIOS:
            await _play(client, harness, scenario)
        listed = await harness.call(client, "begin_task")
        await harness.call(client, "list_rooms", session_id=listed["session_id"])
        await harness.call(
            client,
            "check_availability",
            session_id=listed["session_id"],
            room_id="R3",
            date="2027-03-15",
        )
    payload = json.dumps(harness.responses)
    assert "prescriptive" not in payload
    assert "variant" not in payload
