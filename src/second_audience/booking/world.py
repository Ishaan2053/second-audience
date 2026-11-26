"""The room-booking world: rooms, bookings and the operations agents perform on them.

Pure, synchronous and in-memory. One `World` exists per session. Times are local, on
30-minute steps, and the calendar is fixed to a single working week so that every task
uses explicit ISO dates. `HH:MM` strings exist only at the boundary (`parse_time`,
`format_time`); inside the domain every time is a `datetime.time`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from enum import StrEnum
from typing import ClassVar, Literal, Self

SLOT_MINUTES = 30
WEEK_START = date(2027, 3, 15)  # Monday
WEEK: tuple[date, ...] = tuple(WEEK_START + timedelta(days=offset) for offset in range(5))

BookingStatus = Literal["active", "cancelled"]
CreatedBy = Literal["seed", "agent"]
Details = dict[str, str | int]


class Feature(StrEnum):
    """Equipment a room can have."""

    PROJECTOR = "projector"
    WHITEBOARD = "whiteboard"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class Room:
    """A bookable room with fixed capacity, equipment and opening hours."""

    room_id: str
    capacity: int
    features: frozenset[Feature]
    opens: time
    closes: time


@dataclass(frozen=True, slots=True)
class BookingRequest:
    """The arguments of a booking: what an agent asks for, or what a scenario seeds."""

    room_id: str
    date: date
    start: time
    end: time
    attendees: int
    title: str


@dataclass(frozen=True, slots=True)
class Booking:
    """A booking held by the world. Replaced, never mutated, when it changes."""

    booking_id: str
    room_id: str
    date: date
    start: time
    end: time
    attendees: int
    title: str
    status: BookingStatus
    created_by: CreatedBy


ROOMS: tuple[Room, ...] = (
    Room("R1", 2, frozenset(), time(8), time(20)),
    Room("R2", 4, frozenset({Feature.WHITEBOARD}), time(8), time(20)),
    Room("R3", 6, frozenset({Feature.PROJECTOR, Feature.WHITEBOARD}), time(8), time(20)),
    Room("R4", 8, frozenset({Feature.VIDEO}), time(8), time(18)),
    Room("R5", 10, frozenset({Feature.PROJECTOR, Feature.VIDEO}), time(8), time(20)),
    Room("R6", 16, frozenset(Feature), time(8), time(20)),
)


class DomainError(Exception):
    """A state-dependent failure. Carries a stable `code` and structured `details`, no prose."""

    code: ClassVar[str]

    def __init__(self, details: Details) -> None:
        super().__init__(self.code, details)
        self.details = details


class RoomConflict(DomainError):
    """The requested interval overlaps an active booking in the same room."""

    code = "ROOM_CONFLICT"


class CapacityExceeded(DomainError):
    """More attendees than the room holds."""

    code = "CAPACITY_EXCEEDED"


class OutsideOpeningHours(DomainError):
    """The requested interval is not inside the room's opening hours."""

    code = "OUTSIDE_OPENING_HOURS"


class BookingNotFound(DomainError):
    """No booking has the given ID."""

    code = "BOOKING_NOT_FOUND"


class BookingCancelled(DomainError):
    """The booking exists but has already been cancelled."""

    code = "BOOKING_CANCELLED"


def parse_time(value: str) -> time:
    """Parse a strict `HH:MM` string."""
    if len(value) != 5 or value[2] != ":":
        raise ValueError(f"time must be HH:MM, got {value!r}")
    return time.fromisoformat(value)


def format_time(value: time) -> str:
    """Format a time as `HH:MM`."""
    return value.strftime("%H:%M")


def minutes(value: time) -> int:
    """Minutes since midnight."""
    return value.hour * 60 + value.minute


class World:
    """One session's sandbox: the fixed rooms plus the bookings made so far."""

    def __init__(self, rooms: Iterable[Room] = ROOMS) -> None:
        """Create an empty world with the given rooms."""
        self._rooms = {room.room_id: room for room in rooms}
        self._bookings: dict[str, Booking] = {}

    @classmethod
    def seeded(cls, seed: Iterable[BookingRequest]) -> Self:
        """Create a world with the standard rooms and the given seed bookings applied."""
        world = cls()
        for request in seed:
            world.book(request, created_by="seed")
        return world

    def list_rooms(self) -> list[Room]:
        """All rooms, ordered by ID."""
        return sorted(self._rooms.values(), key=lambda room: room.room_id)

    def room(self, room_id: str) -> Room:
        """Look up a room by ID."""
        try:
            return self._rooms[room_id]
        except KeyError:
            raise ValueError(f"unknown room {room_id!r}") from None

    def bookings(self) -> list[Booking]:
        """Every booking, active or cancelled, in creation order."""
        return list(self._bookings.values())

    def active_bookings(self, created_by: CreatedBy | None = None) -> list[Booking]:
        """Active bookings, optionally only those made by `created_by`."""
        return [
            booking
            for booking in self._bookings.values()
            if booking.status == "active" and created_by in (None, booking.created_by)
        ]

    def free_slots(
        self, room_id: str, day: date, ignore: str | None = None
    ) -> list[tuple[time, time]]:
        """Maximal free intervals of a room on a day, within its opening hours.

        `ignore` treats one booking as absent, as a reschedule of that booking would.
        """
        room = self.room(room_id)
        _check_date(day)
        taken = sorted(
            (booking.start, booking.end) for booking in self._active_in(room_id, day, ignore)
        )
        slots: list[tuple[time, time]] = []
        cursor = room.opens
        for start, end in taken:
            if start > cursor:
                slots.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < room.closes:
            slots.append((cursor, room.closes))
        return slots

    def book(self, request: BookingRequest, created_by: CreatedBy = "agent") -> Booking:
        """Create an active booking, or raise the first domain error that applies."""
        room = self.room(request.room_id)
        if request.attendees < 1:
            raise ValueError("attendees must be at least 1")
        if request.attendees > room.capacity:
            raise CapacityExceeded(
                {
                    "room_id": room.room_id,
                    "capacity": room.capacity,
                    "requested": request.attendees,
                }
            )
        self._check_placement(room, request.date, request.start, request.end, ignore=None)
        booking = Booking(
            booking_id=f"B{len(self._bookings) + 1:03d}",
            room_id=room.room_id,
            date=request.date,
            start=request.start,
            end=request.end,
            attendees=request.attendees,
            title=request.title,
            status="active",
            created_by=created_by,
        )
        self._bookings[booking.booking_id] = booking
        return booking

    def reschedule(self, booking_id: str, day: date, start: time, end: time) -> Booking:
        """Move an active booking to a new date and interval in the same room."""
        booking = self._active(booking_id)
        self._check_placement(self.room(booking.room_id), day, start, end, ignore=booking_id)
        moved = replace(booking, date=day, start=start, end=end)
        self._bookings[booking_id] = moved
        return moved

    def cancel(self, booking_id: str) -> Booking:
        """Cancel an active booking."""
        cancelled = replace(self._active(booking_id), status="cancelled")
        self._bookings[booking_id] = cancelled
        return cancelled

    def _active(self, booking_id: str) -> Booking:
        """Return an active booking or raise `BookingNotFound` / `BookingCancelled`."""
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise BookingNotFound({"booking_id": booking_id})
        if booking.status == "cancelled":
            raise BookingCancelled({"booking_id": booking_id})
        return booking

    def _active_in(self, room_id: str, day: date, ignore: str | None) -> list[Booking]:
        """Active bookings in one room on one day, excluding `ignore`."""
        return [
            booking
            for booking in self.active_bookings()
            if booking.room_id == room_id and booking.date == day and booking.booking_id != ignore
        ]

    def _check_placement(
        self, room: Room, day: date, start: time, end: time, ignore: str | None
    ) -> None:
        """Validate an interval, then check opening hours and conflicts, in that order."""
        _check_date(day)
        for value in (start, end):
            if minutes(value) % SLOT_MINUTES or value.second or value.microsecond:
                raise ValueError(f"times must be on {SLOT_MINUTES}-minute steps")
        if start >= end:
            raise ValueError("start must be before end")
        if start < room.opens or end > room.closes:
            raise OutsideOpeningHours(
                {
                    "room_id": room.room_id,
                    "opens": format_time(room.opens),
                    "closes": format_time(room.closes),
                }
            )
        clashes = [
            booking
            for booking in self._active_in(room.room_id, day, ignore)
            if booking.start < end and start < booking.end  # touching intervals do not clash
        ]
        if clashes:
            first = min(clashes, key=lambda booking: booking.start)
            raise RoomConflict(
                {
                    "room_id": room.room_id,
                    "date": day.isoformat(),
                    "conflict_start": format_time(first.start),
                    "conflict_end": format_time(first.end),
                }
            )


def _check_date(day: date) -> None:
    """Reject dates outside the fixed week."""
    if day not in WEEK:
        raise ValueError(f"date must be within {WEEK[0]} to {WEEK[-1]}, got {day}")
