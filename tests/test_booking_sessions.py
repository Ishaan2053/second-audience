"""SessionManager: budget, finalisation, events, idle expiry and lab/field identification."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, select

from second_audience.booking.scenarios import Scenario, get_scenario
from second_audience.booking.server import booking_payload
from second_audience.booking.sessions import (
    BUDGET_EXHAUSTED,
    INVALID_ARGUMENTS,
    SANDBOX_NOTE,
    SESSION_NOT_FOUND,
    Identity,
    Operation,
    SessionManager,
    identify,
)
from second_audience.booking.suggestions import AttemptedCall
from second_audience.booking.world import BookingRequest, World
from second_audience.core.experiment import NullExperimentClient
from second_audience.core.ledger import Ledger, sessions, tool_calls
from second_audience.core.styles import PRESCRIPTIVE

LAB = Identity("lab", "gemini-test")
IDLE_SECONDS = 600


class FakeClock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A file-backed SQLite ledger database."""
    engine = create_engine(f"sqlite:///{tmp_path / 'sa.db'}")
    yield engine
    engine.dispose()


@pytest.fixture
def experiment() -> NullExperimentClient:
    """An experiment client that puts every session in the prescriptive arm."""
    return NullExperimentClient(style=PRESCRIPTIVE, variant="prescriptive")


@pytest.fixture
def clock() -> FakeClock:
    """The manager's clock."""
    return FakeClock()


def _manager(
    engine: Engine, experiment: NullExperimentClient, clock: FakeClock, max_tool_calls: int = 12
) -> SessionManager:
    return SessionManager(
        Ledger(engine),
        experiment,
        max_tool_calls=max_tool_calls,
        idle_seconds=IDLE_SECONDS,
        clock=clock,
    )


@pytest.fixture
def manager(engine: Engine, experiment: NullExperimentClient, clock: FakeClock) -> SessionManager:
    """A manager with the default budget."""
    return _manager(engine, experiment, clock)


def _book(request: BookingRequest) -> Operation:
    """The world operation behind book_room."""
    return lambda world: {"booking": booking_payload(world.book(request))}


def _book_call(manager: SessionManager, session_id: str, request: BookingRequest) -> dict[str, Any]:
    """Send a book_room call the way the server does."""
    call = AttemptedCall.for_booking(request, session_id=session_id)
    return manager.call(session_id, call.tool, call.arguments, _book(request))


def _list_rooms(manager: SessionManager, session_id: str) -> dict[str, Any]:
    return manager.call(
        session_id,
        "list_rooms",
        {"session_id": session_id},
        lambda world: {"rooms": [r.room_id for r in world.list_rooms()]},
    )


def _row(engine: Engine, session_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(select(sessions).where(sessions.c.session_id == session_id)).one()
    return dict(row._mapping)


def _calls(engine: Engine, session_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(tool_calls)
            .where(tool_calls.c.session_id == session_id)
            .order_by(tool_calls.c.seq)
        ).all()
    return [dict(r._mapping) for r in rows]


def _begin(manager: SessionManager, scenario: Scenario) -> str:
    session_id: str = manager.begin(scenario.id, LAB)["session_id"]
    return session_id


def test_begin_returns_the_brief_and_writes_the_session(
    manager: SessionManager, engine: Engine
) -> None:
    scenario = get_scenario("s01")
    started = manager.begin("s01", LAB)
    assert set(started) == {"session_id", "brief", "max_tool_calls", "note"}
    assert started["brief"] == scenario.brief
    assert started["max_tool_calls"] == 12
    assert started["note"] == SANDBOX_NOTE
    row = _row(engine, started["session_id"])
    assert (row["scenario_id"], row["scenario_kind"]) == ("s01", "aligned")
    assert (row["source"], row["agent_label"]) == ("lab", "gemini-test")
    assert row["variant"] is None
    assert row["exposed_at"] is None


def test_begin_without_scenario_picks_one(manager: SessionManager, engine: Engine) -> None:
    started = manager.begin(None, LAB)
    assert _row(engine, started["session_id"])["scenario_id"].startswith("s")


def test_unknown_session_is_reported(manager: SessionManager) -> None:
    assert _list_rooms(manager, "nope")["error"]["code"] == SESSION_NOT_FOUND
    assert manager.finish("nope", "")["error"]["code"] == SESSION_NOT_FOUND


def test_first_styled_error_records_exposure_once(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient
) -> None:
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)

    first = _book_call(manager, session_id, scenario.first_attempt)
    second = _book_call(manager, session_id, scenario.first_attempt)

    assert first["error"]["code"] == "ROOM_CONFLICT"
    assert "suggestion" in first
    assert "corrected_call" in first
    assert second == first
    assert experiment.resolved == [session_id]
    row = _row(engine, session_id)
    assert (row["variant"], row["in_experiment"]) == ("prescriptive", True)
    assert row["exposed_at"] is not None
    calls = _calls(engine, session_id)
    assert [(c["seq"], c["error_code"], c["styled"]) for c in calls] == [
        (1, "ROOM_CONFLICT", True),
        (2, "ROOM_CONFLICT", True),
    ]


def test_correct_exposed_session_sends_task_completed(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient
) -> None:
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)
    _book_call(manager, session_id, scenario.first_attempt)
    assert "booking" in _book_call(manager, session_id, scenario.solution)

    result = manager.finish(session_id, "Booked R3 at 11:00.")

    assert result == {"session_id": session_id, "finished": True, "outcome": "correct"}
    assert experiment.tracked == [
        (session_id, "task_completed", None),
        (session_id, "task_finished", 2),
    ]
    assert experiment.flushes == 1
    assert experiment.traits[session_id] == {
        "agent_label": "gemini-test",
        "source": "lab",
        "scenario_kind": "aligned",
    }
    row = _row(engine, session_id)
    assert (row["outcome"], row["final_state"], row["tool_call_count"]) == (
        "correct",
        "correct",
        2,
    )
    finish = _calls(engine, session_id)[-1]
    assert (finish["seq"], finish["tool"]) == (3, "finish_task")
    assert finish["arguments"] == {"session_id": session_id, "summary": "Booked R3 at 11:00."}


def test_incorrect_exposed_session_sends_task_wrong_booking(
    manager: SessionManager, experiment: NullExperimentClient
) -> None:
    scenario = get_scenario("s02")  # trap: the suggested room lacks video
    session_id = _begin(manager, scenario)
    error = _book_call(manager, session_id, scenario.first_attempt)
    corrected = error["corrected_call"]["arguments"]
    request = BookingRequest(
        room_id=corrected["room_id"],
        date=scenario.first_attempt.date,
        start=scenario.first_attempt.start,
        end=scenario.first_attempt.end,
        attendees=scenario.first_attempt.attendees,
        title=scenario.first_attempt.title,
    )
    assert "booking" in _book_call(manager, session_id, request)

    assert manager.finish(session_id, "")["outcome"] == "incorrect"
    assert experiment.tracked == [
        (session_id, "task_wrong_booking", None),
        (session_id, "task_finished", 2),
    ]


def test_session_without_styled_error_has_no_variant_and_sends_no_events(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient
) -> None:
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)
    _list_rooms(manager, session_id)
    _book_call(manager, session_id, scenario.solution)

    assert manager.finish(session_id, "")["outcome"] == "correct"
    assert experiment.resolved == []
    assert experiment.tracked == []
    row = _row(engine, session_id)
    assert row["variant"] is None
    assert row["in_experiment"] is None
    assert row["exposed_at"] is None


def test_exposed_session_outside_the_experiment_sends_no_events(
    engine: Engine, clock: FakeClock
) -> None:
    experiment = NullExperimentClient()  # control style, variant None: not enrolled
    manager = _manager(engine, experiment, clock)
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)
    error = _book_call(manager, session_id, scenario.first_attempt)
    _book_call(manager, session_id, scenario.solution)

    assert set(error) == {"error"}
    assert manager.finish(session_id, "")["outcome"] == "correct"
    assert experiment.tracked == []
    row = _row(engine, session_id)
    assert (row["variant"], row["in_experiment"]) == (None, False)
    assert row["exposed_at"] is not None


def test_unstyled_error_is_not_an_exposure(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient
) -> None:
    session_id = _begin(manager, get_scenario("s01"))
    result = manager.call(
        session_id,
        "cancel_booking",
        {"session_id": session_id, "booking_id": "B999"},
        lambda world: {"booking": booking_payload(world.cancel("B999"))},
    )
    assert result == {"error": {"code": "BOOKING_NOT_FOUND", "message": "No booking has ID B999."}}
    assert experiment.resolved == []
    assert _calls(engine, session_id)[0]["styled"] is False


def test_invalid_arguments_are_reported_and_counted(
    manager: SessionManager, engine: Engine
) -> None:
    session_id = _begin(manager, get_scenario("s01"))

    def reversed_interval(world: World) -> dict[str, Any]:
        raise ValueError("start must be before end")

    result = manager.call(session_id, "book_room", {"session_id": session_id}, reversed_interval)
    assert result["error"] == {"code": INVALID_ARGUMENTS, "message": "start must be before end"}
    [call] = _calls(engine, session_id)
    assert (call["seq"], call["error_code"], call["styled"]) == (1, INVALID_ARGUMENTS, False)


def test_budget_exhaustion_finalises_and_scores_the_final_state(
    engine: Engine, experiment: NullExperimentClient, clock: FakeClock
) -> None:
    manager = _manager(engine, experiment, clock, max_tool_calls=2)
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)
    _book_call(manager, session_id, scenario.first_attempt)
    assert "booking" in _book_call(manager, session_id, scenario.solution)

    over = _list_rooms(manager, session_id)
    again = _list_rooms(manager, session_id)

    assert over["error"]["code"] == BUDGET_EXHAUSTED
    assert again == over
    row = _row(engine, session_id)
    assert (row["outcome"], row["final_state"], row["tool_call_count"]) == (
        "budget_exhausted",
        "correct",
        2,
    )
    # Events follow the scored final state.
    assert experiment.tracked == [
        (session_id, "task_completed", None),
        (session_id, "task_finished", 2),
    ]
    # Rejected calls are not in the transcript.
    assert len(_calls(engine, session_id)) == 2
    # finish_task still answers, with the stored outcome, and sends nothing more.
    assert manager.finish(session_id, "")["outcome"] == "budget_exhausted"
    assert len(experiment.tracked) == 2


def test_begin_and_finish_do_not_count_against_the_budget(
    engine: Engine, experiment: NullExperimentClient, clock: FakeClock
) -> None:
    manager = _manager(engine, experiment, clock, max_tool_calls=1)
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)
    assert "booking" in _book_call(manager, session_id, scenario.solution)
    assert manager.finish(session_id, "")["outcome"] == "correct"


def test_finalisation_is_idempotent(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient
) -> None:
    scenario = get_scenario("s01")
    session_id = _begin(manager, scenario)
    _book_call(manager, session_id, scenario.first_attempt)
    _book_call(manager, session_id, scenario.solution)

    first = manager.finish(session_id, "done")
    second = manager.finish(session_id, "done again")

    assert second == first
    assert experiment.tracked == [
        (session_id, "task_completed", None),
        (session_id, "task_finished", 2),
    ]
    assert [c["tool"] for c in _calls(engine, session_id)].count("finish_task") == 1
    # Domain calls after finishing are refused and not recorded.
    assert _list_rooms(manager, session_id)["error"]["code"] == SESSION_NOT_FOUND
    assert len(_calls(engine, session_id)) == 3


def test_idle_sessions_are_abandoned_without_events(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient, clock: FakeClock
) -> None:
    scenario = get_scenario("s01")
    idle_id = _begin(manager, scenario)
    _book_call(manager, idle_id, scenario.first_attempt)
    _book_call(manager, idle_id, scenario.solution)
    active_id = _begin(manager, scenario)

    clock.now += IDLE_SECONDS - 1
    _list_rooms(manager, active_id)  # activity resets the idle timer
    clock.now += 1

    assert manager.expire_idle() == 1
    row = _row(engine, idle_id)
    assert (row["outcome"], row["final_state"]) == ("abandoned", "correct")
    assert experiment.tracked == []
    assert _list_rooms(manager, idle_id)["error"]["code"] == SESSION_NOT_FOUND
    assert manager.finish(idle_id, "")["error"]["code"] == SESSION_NOT_FOUND
    assert _row(engine, active_id)["outcome"] is None
    assert "rooms" in _list_rooms(manager, active_id)


def test_finished_sessions_are_freed_when_idle(
    manager: SessionManager, engine: Engine, clock: FakeClock
) -> None:
    session_id = _begin(manager, get_scenario("s01"))
    manager.finish(session_id, "")
    clock.now += IDLE_SECONDS

    assert manager.expire_idle() == 1
    assert _row(engine, session_id)["outcome"] == "no_booking"
    assert manager.finish(session_id, "")["error"]["code"] == SESSION_NOT_FOUND


def test_close_abandons_open_sessions_and_flushes(
    manager: SessionManager, engine: Engine, experiment: NullExperimentClient
) -> None:
    open_id = _begin(manager, get_scenario("s01"))
    finished_id = _begin(manager, get_scenario("s02"))
    manager.finish(finished_id, "")

    manager.close()

    assert _row(engine, open_id)["outcome"] == "abandoned"
    assert _row(engine, finished_id)["outcome"] == "no_booking"
    assert experiment.flushes == 1
    assert _list_rooms(manager, open_id)["error"]["code"] == SESSION_NOT_FOUND


def test_identify_lab_with_the_right_key() -> None:
    headers = {"x-sa-lab-key": "secret", "x-sa-agent-label": "gemini-3.1-flash-lite"}
    assert identify(headers, "mcp-client", "secret") == Identity("lab", "gemini-3.1-flash-lite")


def test_identify_lab_without_label() -> None:
    assert identify({"x-sa-lab-key": "secret"}, None, "secret") == Identity("lab", "unknown")


@pytest.mark.parametrize(
    ("headers", "lab_key"),
    [
        ({"x-sa-lab-key": "wrong", "x-sa-agent-label": "gemini"}, "secret"),
        ({"x-sa-agent-label": "gemini"}, "secret"),
        ({"x-sa-lab-key": "secret", "x-sa-agent-label": "gemini"}, None),
        ({"x-sa-lab-key": "", "x-sa-agent-label": "gemini"}, ""),
    ],
)
def test_identify_field_without_a_valid_key(headers: dict[str, str], lab_key: str | None) -> None:
    assert identify(headers, "claude-desktop", lab_key) == Identity("field", "claude-desktop")


def test_identify_field_without_client_name_and_truncates_labels() -> None:
    assert identify({}, None, "secret") == Identity("field", "unknown")
    assert len(identify({}, "x" * 500, None).agent_label) == 200
