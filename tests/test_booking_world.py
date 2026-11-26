"""Tests for the booking world: rooms, operations, boundaries and domain errors."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, time

import pytest

from second_audience.booking.world import (
    ROOMS,
    WEEK,
    BookingCancelled,
    BookingNotFound,
    BookingRequest,
    CapacityExceeded,
    Feature,
    OutsideOpeningHours,
    RoomConflict,
    World,
    format_time,
    parse_time,
)

MONDAY = date(2027, 3, 15)


def req(
    room_id: str = "R3", start: str = "10:00", end: str = "11:00", **kw: object
) -> BookingRequest:
    """A valid booking request on Monday, with overrides."""
    base = BookingRequest(room_id, MONDAY, parse_time(start), parse_time(end), 4, "Meeting")
    return replace(base, **kw)  # type: ignore[arg-type]


def test_fixed_week_and_rooms() -> None:
    assert tuple(date(2027, 3, day) for day in range(15, 20)) == WEEK
    assert [room.room_id for room in ROOMS] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    capacities = [room.capacity for room in ROOMS]
    assert min(capacities) == 2
    assert max(capacities) == 16
    assert set().union(*(room.features for room in ROOMS)) == set(Feature)
    closing = [room.closes for room in ROOMS]
    assert closing.count(time(18)) == 1
    assert closing.count(time(20)) == 5
    assert all(room.opens == time(8) for room in ROOMS)


def test_list_rooms_sorted() -> None:
    assert [room.room_id for room in World().list_rooms()] == ["R1", "R2", "R3", "R4", "R5", "R6"]


@pytest.mark.parametrize(("raw", "parsed"), [("08:00", time(8)), ("17:30", time(17, 30))])
def test_time_round_trip(raw: str, parsed: time) -> None:
    assert parse_time(raw) == parsed
    assert format_time(parsed) == raw


@pytest.mark.parametrize("raw", ["8:00", "08:00:00", "0800", "25:00", ""])
def test_parse_time_rejects_non_hh_mm(raw: str) -> None:
    with pytest.raises(ValueError, match=r"HH:MM|Invalid isoformat|hour must be"):
        parse_time(raw)


def test_book_creates_active_agent_booking() -> None:
    world = World()
    booking = world.book(req())
    assert booking.booking_id == "B001"
    assert booking.status == "active"
    assert booking.created_by == "agent"
    assert world.active_bookings(created_by="agent") == [booking]
    assert world.active_bookings(created_by="seed") == []


def test_seeded_bookings_are_marked_seed() -> None:
    world = World.seeded([req()])
    assert [b.created_by for b in world.bookings()] == ["seed"]


def test_back_to_back_bookings_do_not_conflict() -> None:
    world = World()
    world.book(req(start="10:00", end="11:00"))
    world.book(req(start="11:00", end="12:00"))
    world.book(req(start="09:00", end="10:00"))
    assert len(world.active_bookings()) == 3


@pytest.mark.parametrize(
    ("start", "end"),
    [("10:30", "11:30"), ("09:30", "10:30"), ("10:00", "11:00"), ("09:00", "12:00")],
)
def test_overlap_raises_conflict_with_interval(start: str, end: str) -> None:
    world = World.seeded([req(start="10:00", end="11:00")])
    with pytest.raises(RoomConflict) as excinfo:
        world.book(req(start=start, end=end))
    assert excinfo.value.code == "ROOM_CONFLICT"
    assert excinfo.value.details == {
        "room_id": "R3",
        "date": "2027-03-15",
        "conflict_start": "10:00",
        "conflict_end": "11:00",
    }


def test_conflict_reports_earliest_clash() -> None:
    world = World.seeded([req(start="11:00", end="12:00"), req(start="09:00", end="10:00")])
    with pytest.raises(RoomConflict) as excinfo:
        world.book(req(start="09:00", end="12:00"))
    assert excinfo.value.details["conflict_start"] == "09:00"


def test_other_room_or_day_does_not_conflict() -> None:
    world = World.seeded([req()])
    world.book(req(room_id="R5"))
    world.book(req(date=WEEK[1]))


def test_cancelled_booking_frees_the_slot() -> None:
    world = World()
    world.cancel(world.book(req()).booking_id)
    world.book(req())


def test_capacity_exceeded() -> None:
    with pytest.raises(CapacityExceeded) as excinfo:
        World().book(req(room_id="R2", attendees=5))
    assert excinfo.value.code == "CAPACITY_EXCEEDED"
    assert excinfo.value.details == {"room_id": "R2", "capacity": 4, "requested": 5}


def test_capacity_equal_is_allowed() -> None:
    World().book(req(room_id="R2", attendees=4))


def test_end_equal_to_closing_time_is_allowed() -> None:
    World().book(req(room_id="R4", start="17:00", end="18:00"))
    World().book(req(room_id="R5", start="08:00", end="20:00"))


@pytest.mark.parametrize(
    ("room_id", "start", "end", "opens", "closes"),
    [
        ("R4", "17:30", "18:30", "08:00", "18:00"),
        ("R5", "07:30", "08:30", "08:00", "20:00"),
        ("R5", "19:30", "20:30", "08:00", "20:00"),
    ],
)
def test_outside_opening_hours(room_id: str, start: str, end: str, opens: str, closes: str) -> None:
    with pytest.raises(OutsideOpeningHours) as excinfo:
        World().book(req(room_id=room_id, start=start, end=end))
    assert excinfo.value.code == "OUTSIDE_OPENING_HOURS"
    assert excinfo.value.details == {"room_id": room_id, "opens": opens, "closes": closes}


def test_capacity_checked_before_hours_and_conflict() -> None:
    world = World.seeded([req(room_id="R2", attendees=2)])
    with pytest.raises(CapacityExceeded):
        world.book(req(room_id="R2", attendees=9))


def test_hours_checked_before_conflict() -> None:
    world = World.seeded([req(room_id="R4", start="17:00", end="18:00")])
    with pytest.raises(OutsideOpeningHours):
        world.book(req(room_id="R4", start="17:00", end="18:30"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"room_id": "R9"},
        {"date": date(2027, 3, 20)},
        {"date": date(2027, 3, 14)},
        {"start": time(10, 15)},
        {"start": time(11), "end": time(10)},
        {"start": time(10), "end": time(10)},
        {"attendees": 0},
    ],
)
def test_invalid_requests_raise_value_error(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - each case has its own message
        World().book(replace(req(), **overrides))  # type: ignore[arg-type]


def test_free_slots_empty_room_is_opening_hours() -> None:
    world = World()
    assert world.free_slots("R4", MONDAY) == [(time(8), time(18))]


def test_free_slots_around_bookings() -> None:
    world = World.seeded(
        [
            req(start="08:00", end="09:00"),
            req(start="10:00", end="11:00"),
            req(start="11:00", end="12:00"),
            req(start="19:00", end="20:00"),
        ]
    )
    assert world.free_slots("R3", MONDAY) == [
        (time(9), time(10)),
        (time(12), time(19)),
    ]


def test_free_slots_ignore_cancelled() -> None:
    world = World.seeded([req()])
    world.cancel("B001")
    assert world.free_slots("R3", MONDAY) == [(time(8), time(20))]


def test_free_slots_fully_booked() -> None:
    world = World.seeded([req(room_id="R1", start="08:00", end="20:00", attendees=1)])
    assert world.free_slots("R1", MONDAY) == []


def test_reschedule_moves_booking_and_keeps_identity() -> None:
    world = World()
    booking = world.book(req())
    moved = world.reschedule(booking.booking_id, WEEK[2], time(14), time(15, 30))
    assert moved.booking_id == booking.booking_id
    assert (moved.date, moved.start, moved.end) == (WEEK[2], time(14), time(15, 30))
    assert world.bookings() == [moved]


def test_reschedule_may_overlap_its_own_old_interval() -> None:
    world = World()
    booking = world.book(req(start="10:00", end="11:00"))
    world.reschedule(booking.booking_id, MONDAY, time(10, 30), time(11, 30))


def test_reschedule_conflict_and_hours() -> None:
    world = World.seeded([req(start="14:00", end="15:00")])
    booking = world.book(req())
    with pytest.raises(RoomConflict):
        world.reschedule(booking.booking_id, MONDAY, time(14, 30), time(15, 30))
    with pytest.raises(OutsideOpeningHours):
        world.reschedule(booking.booking_id, MONDAY, time(19, 30), time(20, 30))
    assert world.bookings()[1] == booking


def test_cancel_marks_cancelled() -> None:
    world = World()
    booking = world.book(req())
    cancelled = world.cancel(booking.booking_id)
    assert cancelled.status == "cancelled"
    assert world.active_bookings() == []
    assert world.bookings() == [cancelled]


def _cancel(world: World, booking_id: str) -> object:
    """Cancel, as a uniform operation for parametrised tests."""
    return world.cancel(booking_id)


def _reschedule(world: World, booking_id: str) -> object:
    """Reschedule to a free in-hours slot, as a uniform operation for parametrised tests."""
    return world.reschedule(booking_id, MONDAY, time(12), time(13))


OPERATIONS = pytest.mark.parametrize(
    "operation", [_cancel, _reschedule], ids=["cancel", "reschedule"]
)


@OPERATIONS
def test_unknown_booking(operation: Callable[[World, str], object]) -> None:
    with pytest.raises(BookingNotFound) as excinfo:
        operation(World(), "B999")
    assert excinfo.value.code == "BOOKING_NOT_FOUND"
    assert excinfo.value.details == {"booking_id": "B999"}


@OPERATIONS
def test_cancelled_booking_cannot_change(operation: Callable[[World, str], object]) -> None:
    world = World()
    booking_id = world.book(req()).booking_id
    world.cancel(booking_id)
    with pytest.raises(BookingCancelled) as excinfo:
        operation(world, booking_id)
    assert excinfo.value.code == "BOOKING_CANCELLED"
    assert excinfo.value.details == {"booking_id": booking_id}


def test_worlds_are_isolated() -> None:
    first, second = World(), World()
    first.book(req())
    assert second.bookings() == []
