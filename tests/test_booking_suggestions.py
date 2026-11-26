"""The suggestion heuristic: each rule, tie-breaking, no-alternative cases and determinism."""

from datetime import date, time

import pytest

from second_audience.booking.scenarios import SCENARIOS, Scenario
from second_audience.booking.suggestions import (
    BIGGER_ROOM_LATER_REASON,
    BIGGER_ROOM_REASON,
    OPENING_HOURS_REASON,
    OTHER_ROOM_REASON,
    RESCHEDULE_BOOKING,
    SAME_ROOM_REASON,
    AttemptedCall,
    Suggestion,
    suggest,
)
from second_audience.booking.world import (
    BookingRequest,
    DomainError,
    World,
    parse_time,
)

MONDAY = date(2027, 3, 15)


def req(room_id: str, start: str, end: str, attendees: int = 2) -> BookingRequest:
    """A booking request on Monday."""
    return BookingRequest(room_id, MONDAY, parse_time(start), parse_time(end), attendees, "t")


def fail(world: World, request: BookingRequest) -> tuple[DomainError, AttemptedCall]:
    """Attempt a booking that must fail; return the error and the call as sent."""
    with pytest.raises(DomainError) as caught:
        world.book(request)
    return caught.value, AttemptedCall.for_booking(request, session_id="s")


def suggestion_for(world: World, request: BookingRequest) -> Suggestion | None:
    """The heuristic's suggestion for a booking request that fails in `world`."""
    error, call = fail(world, request)
    return suggest(error, world, call)


def slot(room_id: str, start: str, end: str, reason: str) -> Suggestion:
    """An expected Monday suggestion."""
    return Suggestion(room_id, MONDAY, parse_time(start), parse_time(end), reason)


# The suggestion each scenario's first attempt receives, as recorded in scenarios.py.
EXPECTED: dict[str, tuple[str, str, str, str]] = {
    "s01": ("R3", "11:00", "12:00", SAME_ROOM_REASON),
    "s02": ("R3", "10:00", "11:00", BIGGER_ROOM_REASON),
    "s03": ("R2", "15:00", "16:00", SAME_ROOM_REASON),
    "s04": ("R4", "16:30", "18:00", OPENING_HOURS_REASON),
    "s05": ("R3", "12:00", "13:00", SAME_ROOM_REASON),
    "s06": ("R3", "10:00", "11:00", BIGGER_ROOM_REASON),
    "s07": ("R4", "17:00", "18:00", OPENING_HOURS_REASON),
    "s08": ("R2", "11:00", "11:30", OTHER_ROOM_REASON),
    "s09": ("R4", "15:00", "16:00", BIGGER_ROOM_REASON),
    "s10": ("R6", "15:00", "16:00", BIGGER_ROOM_REASON),
    "s11": ("R6", "12:00", "13:00", SAME_ROOM_REASON),
    "s12": ("R5", "08:00", "09:00", OPENING_HOURS_REASON),
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_first_attempt_gets_the_recorded_suggestion(scenario: Scenario) -> None:
    world = scenario.new_world()
    room_id, start, end, reason = EXPECTED[scenario.id]
    assert suggestion_for(world, scenario.first_attempt) == Suggestion(
        room_id, scenario.constraints.date, parse_time(start), parse_time(end), reason
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_suggestion_is_deterministic(scenario: Scenario) -> None:
    world = scenario.new_world()
    error, call = fail(world, scenario.first_attempt)
    first = suggest(error, world, call)
    assert all(suggest(error, world, call) == first for _ in range(5))
    assert suggest(error, scenario.new_world(), call) == first


# Conflict


def test_conflict_tie_goes_later() -> None:
    world = World()
    world.book(req("R2", "10:00", "11:00"))
    assert suggestion_for(world, req("R2", "10:00", "11:00")) == slot(
        "R2", "11:00", "12:00", SAME_ROOM_REASON
    )


def test_conflict_prefers_a_nearer_earlier_slot_over_a_later_one() -> None:
    world = World()
    world.book(req("R2", "10:00", "12:00"))
    assert suggestion_for(world, req("R2", "10:00", "11:00")) == slot(
        "R2", "09:00", "10:00", SAME_ROOM_REASON
    )


def test_conflict_skips_gaps_shorter_than_the_meeting() -> None:
    world = World()
    world.book(req("R2", "08:00", "10:00"))
    world.book(req("R2", "10:30", "12:00"))
    assert suggestion_for(world, req("R2", "10:00", "11:00")) == slot(
        "R2", "12:00", "13:00", SAME_ROOM_REASON
    )


def test_conflict_falls_back_to_the_smallest_other_room_that_fits() -> None:
    world = World()
    world.book(req("R2", "08:00", "20:00"))
    world.book(req("R3", "10:00", "11:00"))
    # R1 is too small for 3; R3 is busy; R4 is the smallest other room that fits and is free.
    assert suggestion_for(world, req("R2", "10:00", "11:00", attendees=3)) == slot(
        "R4", "10:00", "11:00", OTHER_ROOM_REASON
    )


def test_conflict_with_no_alternative_returns_none() -> None:
    world = World()
    for room_id in ("R1", "R2", "R3", "R4", "R5", "R6"):
        world.book(req(room_id, "08:00", "20:00" if room_id != "R4" else "18:00"))
    assert suggestion_for(world, req("R2", "10:00", "11:00")) is None


# Capacity


def test_capacity_with_every_big_room_busy_suggests_the_smallest_ones_nearest_slot() -> None:
    world = World()
    world.book(req("R5", "10:00", "11:00"))
    world.book(req("R6", "09:00", "12:00"))
    assert suggestion_for(world, req("R2", "10:00", "11:00", attendees=9)) == slot(
        "R5", "11:00", "12:00", BIGGER_ROOM_LATER_REASON
    )


def test_capacity_beyond_every_room_returns_none() -> None:
    assert suggestion_for(World(), req("R6", "10:00", "11:00", attendees=17)) is None


def test_capacity_with_the_smallest_big_room_full_all_day_returns_none() -> None:
    world = World()
    world.book(req("R5", "08:00", "20:00"))
    world.book(req("R6", "10:00", "11:00"))
    assert suggestion_for(world, req("R2", "10:00", "11:00", attendees=9)) is None


# Opening hours


def test_opening_hours_before_opening_moves_to_opening_time() -> None:
    assert suggestion_for(World(), req("R2", "07:00", "08:00")) == slot(
        "R2", "08:00", "09:00", OPENING_HOURS_REASON
    )


def test_opening_hours_suggestion_avoids_existing_bookings() -> None:
    world = World()
    world.book(req("R4", "17:00", "18:00"))
    assert suggestion_for(world, req("R4", "17:30", "18:30")) == slot(
        "R4", "16:00", "17:00", OPENING_HOURS_REASON
    )


def test_opening_hours_longer_than_the_day_returns_none() -> None:
    assert suggestion_for(World(), req("R4", "07:00", "18:30")) is None


# Reschedule


def reschedule_call(booking_id: str, start: str, end: str) -> AttemptedCall:
    """A Monday reschedule call as an agent would send it."""
    arguments = {"booking_id": booking_id, "date": MONDAY.isoformat(), "start": start, "end": end}
    return AttemptedCall(RESCHEDULE_BOOKING, {"session_id": "s", **arguments})


def test_reschedule_conflict_ignores_the_booking_being_moved() -> None:
    world = World()
    own = world.book(req("R2", "10:00", "11:00"))
    world.book(req("R2", "11:30", "13:00"))
    with pytest.raises(DomainError) as caught:
        world.reschedule(own.booking_id, MONDAY, time(11, 30), time(12, 30))
    # 10:30-11:30 overlaps the moved booking's current slot, which a reschedule frees.
    suggestion = suggest(caught.value, world, reschedule_call(own.booking_id, "11:30", "12:30"))
    assert suggestion == slot("R2", "10:30", "11:30", SAME_ROOM_REASON)


def test_reschedule_conflict_never_suggests_another_room() -> None:
    world = World()
    own = world.book(req("R2", "10:00", "11:00"))
    world.book(req("R2", "08:00", "10:00"))
    world.book(req("R2", "11:00", "20:00"))
    with pytest.raises(DomainError) as caught:
        world.reschedule(own.booking_id, MONDAY, time(12), time(14))
    # No 2-hour gap in R2. A book_room call would fall back to R3; a reschedule cannot move room.
    assert suggest(caught.value, world, reschedule_call(own.booking_id, "12:00", "14:00")) is None


def test_unknown_tool_is_rejected() -> None:
    error, _ = fail(World(), req("R2", "07:00", "08:00"))
    call = AttemptedCall("cancel_booking", {"booking_id": "B001"})
    with pytest.raises(ValueError, match="no suggestions"):
        suggest(error, World(), call)
