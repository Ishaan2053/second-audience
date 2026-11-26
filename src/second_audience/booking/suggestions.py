"""The fixed suggestion heuristic (docs/02-architecture.md section 3.3).

Given a styled domain error and the call that caused it, propose one alternative slot.
The heuristic sees only the failed call and the world, never the user's brief, which is
what lets trap scenarios exist. It is deterministic: every choice breaks ties explicitly.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Self

from second_audience.booking.world import (
    SLOT_MINUTES,
    BookingRequest,
    CapacityExceeded,
    DomainError,
    OutsideOpeningHours,
    Room,
    RoomConflict,
    World,
    format_time,
    minutes,
    parse_time,
)

BOOK_ROOM = "book_room"
RESCHEDULE_BOOKING = "reschedule_booking"

SAME_ROOM_REASON = "nearest free slot of the same length"
OTHER_ROOM_REASON = "smallest other room free at the requested time"
BIGGER_ROOM_REASON = "smallest room with enough capacity free at the requested time"
BIGGER_ROOM_LATER_REASON = "nearest free slot in the smallest room with enough capacity"
OPENING_HOURS_REASON = "nearest free slot of the same length within opening hours"


@dataclass(frozen=True, slots=True)
class AttemptedCall:
    """A tool call that failed: the tool name and its arguments exactly as the agent sent them."""

    tool: str
    arguments: Mapping[str, Any]

    @classmethod
    def for_booking(cls, request: BookingRequest, **extra: Any) -> Self:
        """The `book_room` call an agent would send for `request`, plus `extra` arguments."""
        return cls(
            BOOK_ROOM,
            {
                **extra,
                "room_id": request.room_id,
                "date": request.date.isoformat(),
                "start": format_time(request.start),
                "end": format_time(request.end),
                "attendees": request.attendees,
                "title": request.title,
            },
        )


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One alternative slot the heuristic proposes, with the rule that produced it."""

    room_id: str
    date: date
    start: time
    end: time
    reason: str

    def to_payload(self) -> dict[str, str]:
        """The suggestion as boundary-style JSON fields."""
        return {
            "room_id": self.room_id,
            "date": self.date.isoformat(),
            "start": format_time(self.start),
            "end": format_time(self.end),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _Placement:
    """Where the failed call tried to put a booking."""

    room_id: str
    date: date
    start: time
    end: time
    attendees: int | None  # None for a reschedule, which cannot change room
    ignore: str | None  # the booking being rescheduled, which does not block itself


def suggest(error: DomainError, world: World, attempted_call: AttemptedCall) -> Suggestion | None:
    """Propose one alternative for a styled error, or `None` if no alternative exists."""
    placement = _placement(world, attempted_call)
    if isinstance(error, RoomConflict):
        same_room = _nearest_in(world, placement.room_id, placement, SAME_ROOM_REASON)
        return same_room or _other_room(world, placement)
    if isinstance(error, CapacityExceeded):
        return _bigger_room(world, placement)
    if isinstance(error, OutsideOpeningHours):
        return _nearest_in(world, placement.room_id, placement, OPENING_HOURS_REASON)
    return None


def _placement(world: World, call: AttemptedCall) -> _Placement:
    """Read the requested room, date and interval from a book or reschedule call."""
    args = call.arguments
    if call.tool == BOOK_ROOM:
        room_id, attendees, ignore = args["room_id"], args["attendees"], None
    elif call.tool == RESCHEDULE_BOOKING:
        ignore = args["booking_id"]
        room_id = next(b.room_id for b in world.bookings() if b.booking_id == ignore)
        attendees = None
    else:
        raise ValueError(f"no suggestions for tool {call.tool!r}")
    day = date.fromisoformat(args["date"])
    return _Placement(
        room_id, day, parse_time(args["start"]), parse_time(args["end"]), attendees, ignore
    )


def _other_room(world: World, placement: _Placement) -> Suggestion | None:
    """The smallest other room that fits the attendees and is free at the requested time."""
    if placement.attendees is None:
        return None
    room = _smallest_room(world, placement.attendees, placement, exclude=placement.room_id)
    return _at_requested_time(room, placement, OTHER_ROOM_REASON) if room else None


def _bigger_room(world: World, placement: _Placement) -> Suggestion | None:
    """The smallest big-enough room free then; failing that, that room's nearest free slot."""
    if placement.attendees is None:
        return None
    free = _smallest_room(world, placement.attendees, placement, exclude=None)
    if free:
        return _at_requested_time(free, placement, BIGGER_ROOM_REASON)
    big_enough = [room for room in world.list_rooms() if room.capacity >= placement.attendees]
    if not big_enough:
        return None
    smallest = min(big_enough, key=lambda room: (room.capacity, room.room_id))
    return _nearest_in(world, smallest.room_id, placement, BIGGER_ROOM_LATER_REASON)


def _nearest_in(
    world: World, room_id: str, placement: _Placement, reason: str
) -> Suggestion | None:
    """Nearest free start of the requested length in a room on the requested day; ties go later."""
    length = minutes(placement.end) - minutes(placement.start)
    target = minutes(placement.start)
    starts = [
        start
        for low, high in world.free_slots(room_id, placement.date, placement.ignore)
        for start in range(minutes(low), minutes(high) - length + 1, SLOT_MINUTES)
    ]
    best = min(starts, key=lambda start: (abs(start - target), -start), default=None)
    if best is None:
        return None
    return Suggestion(room_id, placement.date, _clock(best), _clock(best + length), reason)


def _smallest_room(
    world: World, attendees: int, placement: _Placement, exclude: str | None
) -> Room | None:
    """The smallest room (then lowest ID) that fits `attendees` and is free at the time."""
    candidates = [
        room
        for room in world.list_rooms()
        if room.room_id != exclude
        and room.capacity >= attendees
        and any(
            low <= placement.start and placement.end <= high
            for low, high in world.free_slots(room.room_id, placement.date)
        )
    ]
    return min(candidates, key=lambda room: (room.capacity, room.room_id), default=None)


def _at_requested_time(room: Room, placement: _Placement, reason: str) -> Suggestion:
    """A suggestion for `room` at exactly the requested date and interval."""
    return Suggestion(room.room_id, placement.date, placement.start, placement.end, reason)


def _clock(minute_of_day: int) -> time:
    """Convert minutes since midnight back to a time."""
    return time(minute_of_day // 60, minute_of_day % 60)
