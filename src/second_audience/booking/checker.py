"""Deterministic correctness check of a session's final state against its scenario."""

from enum import StrEnum

from second_audience.booking.scenarios import Constraints, Scenario
from second_audience.booking.world import Booking, World, minutes


class Outcome(StrEnum):
    """How a session ended. The checker assigns the first three; the session layer the rest."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    NO_BOOKING = "no_booking"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ABANDONED = "abandoned"


def satisfies(world: World, booking: Booking, constraints: Constraints) -> bool:
    """Whether one booking meets every constraint of the brief."""
    room = world.room(booking.room_id)
    return (
        booking.date == constraints.date
        and booking.start >= constraints.earliest_start
        and booking.end <= constraints.latest_end
        and minutes(booking.end) - minutes(booking.start) == constraints.duration_minutes
        and room.capacity >= constraints.attendees
        and constraints.required_features <= room.features
    )


def check(world: World, scenario: Scenario) -> Outcome:
    """Score the final state: exactly one active agent booking that satisfies the brief."""
    made = world.active_bookings(created_by="agent")
    if not made:
        return Outcome.NO_BOOKING
    if len(made) == 1 and satisfies(world, made[0], scenario.constraints):
        return Outcome.CORRECT
    return Outcome.INCORRECT
