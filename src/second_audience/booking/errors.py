"""Render domain errors into the response payloads under test.

Both arms share one message per error code, so the only difference between arms is the
optional fields the style adds. Errors outside `STYLED_CODES` render identically in every
style and do not count as exposure.
"""

from dataclasses import dataclass
from typing import Any

from second_audience.booking.suggestions import BOOK_ROOM, AttemptedCall, Suggestion, suggest
from second_audience.booking.world import (
    BookingCancelled,
    BookingNotFound,
    CapacityExceeded,
    DomainError,
    OutsideOpeningHours,
    RoomConflict,
    World,
    format_time,
)
from second_audience.core.styles import ErrorStyle

STYLED_CODES = frozenset({RoomConflict.code, CapacityExceeded.code, OutsideOpeningHours.code})

_MESSAGES: dict[str, str] = {
    RoomConflict.code: (
        "Room {room_id} is already booked {conflict_start}-{conflict_end} on {date}."
    ),
    CapacityExceeded.code: (
        "Room {room_id} holds at most {capacity} people; the booking is for {requested}."
    ),
    OutsideOpeningHours.code: (
        "Room {room_id} is open {opens}-{closes}; the requested time is outside these hours."
    ),
    BookingNotFound.code: "No booking has ID {booking_id}.",
    BookingCancelled.code: "Booking {booking_id} has already been cancelled.",
}

CAUTION_NOTE = (
    "This suggestion was generated without knowing the user's requirements. "
    "Check it against them before using it."
)


@dataclass(frozen=True, slots=True)
class RenderedError:
    """A rendered error payload, and whether it was a styled error (which counts as exposure)."""

    payload: dict[str, Any]
    styled: bool


def error_payload(code: str, message: str) -> dict[str, Any]:
    """The base error payload every style shares: a stable code and a plain message."""
    return {"error": {"code": code, "message": message}}


def error_message(error: DomainError) -> str:
    """The plain statement of fact for an error, identical in every style."""
    return _MESSAGES[error.code].format(**error.details)


def render_error(
    error: DomainError, world: World, style: ErrorStyle, attempted_call: AttemptedCall
) -> RenderedError:
    """Render an error in the given style; suggestion fields appear only if one exists."""
    payload = error_payload(error.code, error_message(error))
    styled = error.code in STYLED_CODES
    if not (styled and style.suggest_alternative):
        return RenderedError(payload, styled)
    suggestion = suggest(error, world, attempted_call)
    if suggestion is None:
        return RenderedError(payload, styled)
    payload["suggestion"] = suggestion.to_payload()
    if style.corrected_call:
        payload["corrected_call"] = {
            "tool": attempted_call.tool,
            "arguments": corrected_arguments(attempted_call, suggestion),
        }
    if style.caution_note:
        payload["note"] = CAUTION_NOTE
    return RenderedError(payload, styled)


def corrected_arguments(call: AttemptedCall, suggestion: Suggestion) -> dict[str, Any]:
    """The attempted call's arguments with the suggested room, date and interval applied."""
    arguments = dict(call.arguments)
    if call.tool == BOOK_ROOM:
        arguments["room_id"] = suggestion.room_id
    arguments["date"] = suggestion.date.isoformat()
    arguments["start"] = format_time(suggestion.start)
    arguments["end"] = format_time(suggestion.end)
    return arguments
