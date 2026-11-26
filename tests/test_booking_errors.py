"""Error rendering per style, arm parity, and the trap/aligned property of every scenario."""

import json
from datetime import date
from typing import Any

import pytest

from second_audience.booking.checker import Outcome, check
from second_audience.booking.errors import (
    CAUTION_NOTE,
    STYLED_CODES,
    RenderedError,
    render_error,
)
from second_audience.booking.scenarios import SCENARIOS, Scenario
from second_audience.booking.suggestions import BOOK_ROOM, AttemptedCall
from second_audience.booking.world import (
    BookingCancelled,
    BookingNotFound,
    BookingRequest,
    DomainError,
    World,
    parse_time,
)
from second_audience.core.styles import CONTROL, PRESCRIPTIVE, ErrorStyle

MONDAY = date(2027, 3, 15)
ALL_STYLES = [
    CONTROL,
    ErrorStyle(suggest_alternative=True),
    PRESCRIPTIVE,
    ErrorStyle(suggest_alternative=True, caution_note=True),
    ErrorStyle(suggest_alternative=True, corrected_call=True, caution_note=True),
    ErrorStyle(caution_note=True),
]
BY_ID = pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)


def first_error(scenario: Scenario) -> tuple[World, DomainError, AttemptedCall]:
    """Run the scenario's first attempt; return the world, the error and the call as sent."""
    world = scenario.new_world()
    with pytest.raises(DomainError) as caught:
        world.book(scenario.first_attempt)
    return world, caught.value, AttemptedCall.for_booking(scenario.first_attempt, session_id="x")


def render_first(scenario: Scenario, style: ErrorStyle) -> RenderedError:
    """Render the scenario's first error in `style`."""
    world, error, call = first_error(scenario)
    return render_error(error, world, style, call)


def apply_book_call(world: World, call: dict[str, Any]) -> None:
    """Replay a `corrected_call` payload against the world, as the book_room tool would."""
    assert call["tool"] == BOOK_ROOM
    args = call["arguments"]
    world.book(
        BookingRequest(
            room_id=args["room_id"],
            date=date.fromisoformat(args["date"]),
            start=parse_time(args["start"]),
            end=parse_time(args["end"]),
            attendees=args["attendees"],
            title=args["title"],
        )
    )


# Messages and parity


def test_conflict_message_states_the_taken_interval() -> None:
    rendered = render_first(SCENARIOS[0], CONTROL)  # s01: R3 taken 10:00-11:00 on Monday
    assert rendered.payload == {
        "error": {
            "code": "ROOM_CONFLICT",
            "message": "Room R3 is already booked 10:00-11:00 on 2027-03-15.",
        }
    }
    assert rendered.styled is True


@pytest.mark.parametrize(
    ("scenario_id", "code", "message"),
    [
        ("s02", "CAPACITY_EXCEEDED", "Room R2 holds at most 4 people; the booking is for 5."),
        (
            "s04",
            "OUTSIDE_OPENING_HOURS",
            "Room R4 is open 08:00-18:00; the requested time is outside these hours.",
        ),
    ],
)
def test_styled_messages_state_the_fact(scenario_id: str, code: str, message: str) -> None:
    scenario = next(s for s in SCENARIOS if s.id == scenario_id)
    assert render_first(scenario, CONTROL).payload == {"error": {"code": code, "message": message}}


@BY_ID
def test_prescriptive_is_informative_plus_suggestion_and_corrected_call(
    scenario: Scenario,
) -> None:
    informative = render_first(scenario, CONTROL).payload
    prescriptive = render_first(scenario, PRESCRIPTIVE).payload
    assert set(prescriptive) == {"error", "suggestion", "corrected_call"}
    assert json.dumps(prescriptive["error"]).encode() == json.dumps(informative["error"]).encode()
    assert {"error": prescriptive["error"]} == informative


@BY_ID
@pytest.mark.parametrize("style", ALL_STYLES)
def test_first_error_is_styled_in_every_style(scenario: Scenario, style: ErrorStyle) -> None:
    rendered = render_first(scenario, style)
    assert rendered.styled is True
    assert rendered.payload["error"]["code"] in STYLED_CODES


# Each style field


def test_suggest_alternative_alone_adds_only_the_suggestion() -> None:
    payload = render_first(SCENARIOS[0], ErrorStyle(suggest_alternative=True)).payload
    assert payload["suggestion"] == {
        "room_id": "R3",
        "date": "2027-03-15",
        "start": "11:00",
        "end": "12:00",
        "reason": "nearest free slot of the same length",
    }
    assert set(payload) == {"error", "suggestion"}


def test_corrected_call_keeps_the_attempted_tool_and_extra_arguments() -> None:
    payload = render_first(SCENARIOS[1], PRESCRIPTIVE).payload  # s02: capacity, R2 -> R3
    assert payload["corrected_call"] == {
        "tool": "book_room",
        "arguments": {
            "session_id": "x",
            "room_id": "R3",
            "date": "2027-03-17",
            "start": "10:00",
            "end": "11:00",
            "attendees": 5,
            "title": "Customer call",
        },
    }


def test_corrected_call_for_a_reschedule_changes_only_the_slot() -> None:
    world = World()
    own = world.book(BookingRequest("R2", MONDAY, parse_time("09:00"), parse_time("10:00"), 2, "t"))
    with pytest.raises(DomainError) as caught:
        world.reschedule(own.booking_id, MONDAY, parse_time("19:30"), parse_time("20:30"))
    call = AttemptedCall(
        "reschedule_booking",
        {
            "session_id": "x",
            "booking_id": own.booking_id,
            "date": "2027-03-15",
            "start": "19:30",
            "end": "20:30",
        },
    )
    payload = render_error(caught.value, world, PRESCRIPTIVE, call).payload
    assert payload["corrected_call"] == {
        "tool": "reschedule_booking",
        "arguments": {
            "session_id": "x",
            "booking_id": own.booking_id,
            "date": "2027-03-15",
            "start": "19:00",
            "end": "20:00",
        },
    }


def test_caution_note_follows_the_suggestion() -> None:
    style = ErrorStyle(suggest_alternative=True, corrected_call=True, caution_note=True)
    payload = render_first(SCENARIOS[0], style).payload
    assert payload["note"] == CAUTION_NOTE
    assert list(payload) == ["error", "suggestion", "corrected_call", "note"]


def test_caution_note_without_a_suggestion_adds_nothing() -> None:
    assert render_first(SCENARIOS[0], ErrorStyle(caution_note=True)).payload == (
        render_first(SCENARIOS[0], CONTROL).payload
    )


def test_no_alternative_omits_every_suggestion_field() -> None:
    world = World()
    request = BookingRequest("R6", MONDAY, parse_time("10:00"), parse_time("11:00"), 17, "t")
    with pytest.raises(DomainError) as caught:
        world.book(request)
    style = ErrorStyle(suggest_alternative=True, corrected_call=True, caution_note=True)
    rendered = render_error(caught.value, world, style, AttemptedCall.for_booking(request))
    assert set(rendered.payload) == {"error"}
    assert rendered.styled is True


# Unstyled errors


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (BookingNotFound({"booking_id": "B042"}), "No booking has ID B042."),
        (BookingCancelled({"booking_id": "B001"}), "Booking B001 has already been cancelled."),
    ],
)
@pytest.mark.parametrize("style", ALL_STYLES)
def test_unstyled_errors_render_identically_in_every_style(
    error: DomainError, message: str, style: ErrorStyle
) -> None:
    call = AttemptedCall("cancel_booking", {"session_id": "x", "booking_id": "B042"})
    rendered = render_error(error, World(), style, call)
    assert rendered == RenderedError({"error": {"code": error.code, "message": message}}, False)


# Trap and aligned scenarios


@BY_ID
def test_corrected_call_scores_as_the_scenario_kind_predicts(scenario: Scenario) -> None:
    world, error, call = first_error(scenario)
    corrected = render_error(error, world, PRESCRIPTIVE, call).payload["corrected_call"]
    apply_book_call(world, corrected)
    expected = Outcome.CORRECT if scenario.kind == "aligned" else Outcome.INCORRECT
    assert check(world, scenario) is expected
