"""The lab runner against the real booking server in-process, with scripted fake models."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import asgi_client
from pydantic import SecretStr
from sqlalchemy import create_engine, select

from second_audience.booking.scenarios import SCENARIOS, Scenario
from second_audience.booking.server import build_server
from second_audience.booking.sessions import SessionManager
from second_audience.booking.world import format_time
from second_audience.config import Settings, get_settings
from second_audience.core.experiment import NullExperimentClient
from second_audience.core.ledger import (
    Ledger,
    PlannedRun,
    RequestStatus,
    lab_runs,
    sessions,
    tool_calls,
)
from second_audience.core.styles import ARM_FLAG_VALUES, ErrorStyle
from second_audience.lab.models import (
    GeminiModelClient,
    Message,
    ModelClient,
    ModelTurn,
    QuotaExhaustedError,
    RetryLaterError,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)
from second_audience.lab.runner import (
    AGENT_LABEL_HEADER,
    LAB_KEY_HEADER,
    MAX_MODEL_TURNS,
    LabRunner,
    last_quota_reset,
    next_quota_reset,
)

LAB_KEY = "test-lab-key"
Script = Callable[[Sequence[Message], Sequence[ToolSpec]], Awaitable[ModelTurn]]


class FakeModel:
    """A `ModelClient` whose turns come from a script; it records every conversation it saw."""

    sdk_version = "fake 1.0"

    def __init__(self, name: str, script: Script) -> None:
        self.name = name
        self.script = script
        self.seen: list[list[Message]] = []
        self.tools: list[str] = []

    async def step(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        self.seen.append(list(messages))
        self.tools = [tool.name for tool in tools]
        return await self.script(messages, tools)


def session_id_of(messages: Sequence[Message]) -> str:
    """The session_id from the runner's opening user message."""
    first = messages[0]
    assert isinstance(first, UserMessage)
    match = re.search(r"session_id is ([0-9a-f]+)", first.text)
    assert match
    return match.group(1)


def scenario_of(messages: Sequence[Message]) -> Scenario:
    """The scenario whose brief opens the conversation."""
    first = messages[0]
    assert isinstance(first, UserMessage)
    return next(s for s in SCENARIOS if first.text.startswith(s.brief))


def call(name: str, **arguments: Any) -> ModelTurn:
    """A model turn with one tool call."""
    return ModelTurn(text="", tool_calls=[ToolCall(name, arguments, call_id=f"c-{name}")])


def finish(messages: Sequence[Message]) -> ModelTurn:
    return call("finish_task", session_id=session_id_of(messages), summary="Done.")


def first_attempt(messages: Sequence[Message]) -> ModelTurn:
    """The booking the brief invites, which fails on a state-dependent error."""
    attempt = scenario_of(messages).first_attempt
    return call(
        "book_room",
        session_id=session_id_of(messages),
        room_id=attempt.room_id,
        date=attempt.date.isoformat(),
        start=format_time(attempt.start),
        end=format_time(attempt.end),
        attendees=attempt.attendees,
        title=attempt.title,
    )


async def list_then_finish(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
    if len(messages) == 1:
        return call("list_rooms", session_id=session_id_of(messages))
    return finish(messages)


async def attempt_then_finish(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
    if len(messages) == 1:
        return first_attempt(messages)
    return finish(messages)


class Lab:
    """An in-process server and a ledger shared by the server and the runner."""

    def __init__(self, tmp_path: Path, variant: str = "control") -> None:
        self.engine = create_engine(f"sqlite:///{tmp_path / 'lab.db'}")
        self.ledger = Ledger(self.engine)
        self.experiment = NullExperimentClient(
            style=ErrorStyle.from_flag_value(ARM_FLAG_VALUES[variant]), variant=variant
        )
        manager = SessionManager(self.ledger, self.experiment, max_tool_calls=50, idle_seconds=600)
        self.server: FastMCP = build_server(
            manager, Settings(sa_env="test", sa_lab_key=SecretStr(LAB_KEY))
        )
        self.sleeps: list[float] = []

    def open_session(self, model: str) -> AbstractAsyncContextManager[Client]:
        return asgi_client(
            self.server, headers={LAB_KEY_HEADER: LAB_KEY, AGENT_LABEL_HEADER: model}
        )

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def runner(self, *models: ModelClient, requests_per_day: int = 1000) -> LabRunner:
        return LabRunner(
            self.ledger,
            {model.name: model for model in models},
            self.open_session,
            requests_per_minute=600,
            requests_per_day=requests_per_day,
            sleep=self.sleep,
        )

    def plan(self, model: str, *scenario_ids: str) -> None:
        self.ledger.plan_runs(PlannedRun(model, sid, 1) for sid in scenario_ids)

    def rows(self, table: Any) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(select(table)))


@pytest.fixture
async def lab(tmp_path: Path) -> AsyncIterator[Lab]:
    lab = Lab(tmp_path)
    yield lab
    lab.engine.dispose()


async def test_full_loop_records_a_lab_session(lab: Lab) -> None:
    model = FakeModel("m1", list_then_finish)
    lab.plan("m1", "s01")
    summary = await lab.runner(model).run()

    assert (summary.done, summary.failed) == (1, 0)
    [run] = lab.rows(lab_runs)
    [session] = lab.rows(sessions)
    assert run.status == "done"
    assert run.session_id == session.session_id
    assert (session.source, session.agent_label, session.scenario_id) == ("lab", "m1", "s01")
    assert session.outcome is not None
    assert [c.tool for c in lab.rows(tool_calls)] == ["list_rooms", "finish_task"]
    assert "begin_task" not in model.tools
    assert len(model.seen) == 2  # finish_task ends the loop without another model call
    assert lab.ledger.requests_per_done_run() == {"m1": 2.0}


async def test_turn_limit_then_runner_finishes(lab: Lab) -> None:
    async def forever(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        return call("list_rooms", session_id=session_id_of(messages))

    model = FakeModel("m1", forever)
    lab.plan("m1", "s01")
    await lab.runner(model).run()

    assert len(model.seen) == MAX_MODEL_TURNS
    calls = lab.rows(tool_calls)
    assert [c.tool for c in calls] == ["list_rooms"] * MAX_MODEL_TURNS + ["finish_task"]
    assert calls[-1].arguments["summary"] == "Closed by the lab runner: turn limit reached."


async def test_runner_finishes_when_the_model_stops(lab: Lab) -> None:
    async def stop(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        return ModelTurn(text="I cannot help with that.")

    lab.plan("m1", "s01")
    await lab.runner(FakeModel("m1", stop)).run()

    [finish_call] = lab.rows(tool_calls)
    assert finish_call.tool == "finish_task"
    assert finish_call.arguments["summary"] == "Closed by the lab runner: model stopped."
    [run] = lab.rows(lab_runs)
    assert run.status == "done"


async def test_tool_errors_and_unoffered_tools_go_back_to_the_model(lab: Lab) -> None:
    async def script(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        if len(messages) == 1:
            return ModelTurn(
                text="",
                tool_calls=[
                    ToolCall("begin_task", {"scenario_id": "s02"}),
                    ToolCall("list_rooms", {"session_id": 5}),  # wrong type: schema error
                ],
            )
        return finish(messages)

    model = FakeModel("m1", script)
    lab.plan("m1", "s01")
    await lab.runner(model).run()

    results = [m for m in model.seen[1] if isinstance(m, ToolResult)]
    assert results[0].result == {"error": "Unknown tool 'begin_task'."}
    assert "error" in results[1].result
    assert len(lab.rows(sessions)) == 1  # the model could not start a second session


async def test_daily_quota_parks_one_model_while_others_continue(lab: Lab) -> None:
    async def exhausted(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        raise QuotaExhaustedError("daily quota exhausted")

    lab.plan("parked", "s01", "s02")
    lab.plan("healthy", "s01", "s02", "s03")
    parked, healthy = FakeModel("parked", exhausted), FakeModel("healthy", list_then_finish)
    summary = await lab.runner(parked, healthy).run()

    assert len(parked.seen) == 1  # parked after the first refusal; no further requests
    assert set(summary.parked) == {"parked"}
    assert summary.parked["parked"] > datetime.now(UTC)
    assert (summary.done, summary.requeued) == (3, 1)
    assert lab.ledger.run_counts() == {"parked": {"planned": 2}, "healthy": {"done": 3}}


async def test_local_daily_budget_parks_before_any_request(lab: Lab) -> None:
    lab.ledger.record_model_request("m1", None, RequestStatus.OK)
    lab.plan("m1", "s01")
    model = FakeModel("m1", list_then_finish)
    summary = await lab.runner(model, requests_per_day=1).run()

    assert model.seen == []
    assert set(summary.parked) == {"m1"}
    assert lab.ledger.run_counts() == {"m1": {"planned": 1}}


async def test_rate_limit_backs_off_and_retries_the_same_turn(lab: Lab) -> None:
    refusals = [RetryLaterError(retry_after=7.0)]

    async def script(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        if refusals:
            raise refusals.pop()
        return await list_then_finish(messages, tools)

    model = FakeModel("m1", script)
    lab.plan("m1", "s01")
    summary = await lab.runner(model).run()

    assert summary.done == 1
    assert 7.0 in lab.sleeps
    assert model.seen[0] == model.seen[1]  # the retried turn sent the same conversation
    since = datetime(2000, 1, 1, tzinfo=UTC)
    assert lab.ledger.model_requests_since(since) == {"m1": 2}  # the refusal is not counted


async def test_other_errors_fail_the_run_and_move_on(lab: Lab) -> None:
    calls = 0

    async def flaky(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("model exploded")
        return await list_then_finish(messages, tools)

    lab.plan("m1", "s01", "s02")
    summary = await lab.runner(FakeModel("m1", flaky)).run()

    assert (summary.done, summary.failed) == (1, 1)
    failed = [r for r in lab.rows(lab_runs) if r.status == "failed"]
    assert failed[0].failure_reason == "RuntimeError: model exploded"


async def test_interrupt_requeues_and_resume_skips_completed_runs(lab: Lab) -> None:
    started, never = asyncio.Event(), asyncio.Event()
    completed = 0

    async def hangs_on_second(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        nonlocal completed
        if completed == 1 and len(messages) == 1:
            started.set()
            await never.wait()
        turn = await list_then_finish(messages, tools)
        completed += turn.tool_calls[0].name == "finish_task"
        return turn

    lab.plan("m1", "s01", "s02", "s03")
    task = asyncio.create_task(lab.runner(FakeModel("m1", hangs_on_second)).run())
    await started.wait()
    task.cancel()  # what Ctrl+C does to asyncio.run's main task
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lab.ledger.run_counts() == {"m1": {"done": 1, "planned": 2}}

    resumed = FakeModel("m1", list_then_finish)
    summary = await lab.runner(resumed).run()
    assert summary.done == 2
    assert len(resumed.seen) == 4  # two runs, two turns each: the done run was not repeated
    assert lab.ledger.run_counts() == {"m1": {"done": 3}}
    outcomes = sorted(str(s.outcome) for s in lab.rows(sessions))
    assert outcomes.count("abandoned") == 1  # the interrupted session, closed by the server


async def test_no_message_or_log_line_contains_the_variant(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lab = Lab(tmp_path, variant="prescriptive")
    model = FakeModel("m1", attempt_then_finish)
    lab.plan("m1", *(s.id for s in SCENARIOS))
    with caplog.at_level(logging.DEBUG, logger="second_audience.lab"):
        await lab.runner(model).run()

    exposed = lab.ledger.session_summaries(r.session_id for r in lab.rows(sessions))
    assert any(s.variant == "prescriptive" for s in exposed)  # styled errors were served
    sent = repr(model.seen) + repr(model.tools)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for key in ("prescriptive", "control"):
        assert key not in sent
        assert key not in logged
    lab.engine.dispose()


def test_quota_reset_is_midnight_pacific() -> None:
    now = datetime(2026, 11, 27, 5, 0, tzinfo=UTC)  # 21:00 PST on Nov 26
    assert last_quota_reset(now) == datetime(2026, 11, 26, 8, 0, tzinfo=UTC)
    assert next_quota_reset(now) == datetime(2026, 11, 27, 8, 0, tzinfo=UTC)


@pytest.mark.network
async def test_one_real_session_per_configured_model(lab: Lab) -> None:
    """Drive one real Gemini session per configured model against the in-process server."""
    settings = get_settings()
    if settings.gemini_api_key is None or not settings.sa_lab_models:
        pytest.skip("GEMINI_API_KEY and SA_LAB_MODELS are needed")
    key = settings.gemini_api_key.get_secret_value()
    clients = [GeminiModelClient(model, key) for model in settings.sa_lab_models]
    for client in clients:
        lab.plan(client.name, "s01")
    summary = await lab.runner(*clients).run()
    assert (summary.done, summary.failed, summary.parked) == (len(clients), 0, {})
    assert {s.agent_label for s in lab.rows(sessions)} == set(settings.sa_lab_models)
