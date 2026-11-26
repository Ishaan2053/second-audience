"""Tests for `check`: each way a final state can fail, on top of a correct baseline."""

from dataclasses import replace
from datetime import time

import pytest

from second_audience.booking.checker import Outcome, check
from second_audience.booking.scenarios import get_scenario

# s09: 7 people, projector, 2027-03-15, 14:00-17:00, 60 minutes. Solution is R5 15:00-16:00.
SCENARIO = get_scenario("s09")
SOLUTION = SCENARIO.solution


def outcome_after(*requests: object) -> Outcome:
    """Book each request in a fresh scenario world and score the result."""
    world = SCENARIO.new_world()
    for request in requests:
        world.book(request)  # type: ignore[arg-type]
    return check(world, SCENARIO)


def test_solution_is_correct() -> None:
    assert outcome_after(SOLUTION) is Outcome.CORRECT


@pytest.mark.parametrize(
    "wrong",
    [
        pytest.param(replace(SOLUTION, start=time(13), end=time(14)), id="starts-too-early"),
        pytest.param(replace(SOLUTION, start=time(16, 30), end=time(17, 30)), id="ends-too-late"),
        pytest.param(replace(SOLUTION, date=SOLUTION.date.replace(day=16)), id="wrong-date"),
        pytest.param(replace(SOLUTION, end=time(16, 30)), id="wrong-duration"),
        pytest.param(replace(SOLUTION, room_id="R4"), id="missing-feature"),
        pytest.param(replace(SOLUTION, room_id="R3", attendees=6), id="insufficient-capacity"),
    ],
)
def test_single_wrong_booking_is_incorrect(wrong: object) -> None:
    assert outcome_after(wrong) is Outcome.INCORRECT


def test_two_active_agent_bookings_are_incorrect() -> None:
    second = replace(SOLUTION, room_id="R6")
    assert outcome_after(SOLUTION, second) is Outcome.INCORRECT


def test_cancelled_extra_booking_does_not_count() -> None:
    world = SCENARIO.new_world()
    world.book(SOLUTION)
    world.cancel(world.book(replace(SOLUTION, room_id="R6")).booking_id)
    assert check(world, SCENARIO) is Outcome.CORRECT


def test_no_agent_booking() -> None:
    assert outcome_after() is Outcome.NO_BOOKING


def test_only_cancelled_agent_booking_is_no_booking() -> None:
    world = SCENARIO.new_world()
    world.cancel(world.book(SOLUTION).booking_id)
    assert check(world, SCENARIO) is Outcome.NO_BOOKING


def test_rescheduled_into_window_is_correct() -> None:
    world = SCENARIO.new_world()
    booking = world.book(replace(SOLUTION, start=time(17), end=time(18)))
    assert check(world, SCENARIO) is Outcome.INCORRECT
    world.reschedule(booking.booking_id, SOLUTION.date, SOLUTION.start, SOLUTION.end)
    assert check(world, SCENARIO) is Outcome.CORRECT


def test_seed_bookings_are_ignored() -> None:
    scenario = get_scenario("s01")  # has a seed booking
    assert check(scenario.new_world(), scenario) is Outcome.NO_BOOKING


def test_session_layer_outcomes_exist() -> None:
    assert {o.value for o in Outcome} == {
        "correct",
        "incorrect",
        "no_booking",
        "budget_exhausted",
        "abandoned",
    }
