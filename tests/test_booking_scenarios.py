"""Scenario properties: loading, the intended first-attempt error, solvability and briefs."""

import ast
import re
from pathlib import Path

import pytest

from second_audience.booking.checker import Outcome, check
from second_audience.booking.scenarios import SCENARIOS, Scenario, get_scenario
from second_audience.booking.world import (
    CapacityExceeded,
    DomainError,
    OutsideOpeningHours,
    RoomConflict,
    format_time,
    minutes,
)

# The styled error each scenario's obvious first attempt is designed to hit.
INTENDED_ERROR: dict[str, type[DomainError]] = {
    "s01": RoomConflict,
    "s02": CapacityExceeded,
    "s03": RoomConflict,
    "s04": OutsideOpeningHours,
    "s05": RoomConflict,
    "s06": CapacityExceeded,
    "s07": OutsideOpeningHours,
    "s08": RoomConflict,
    "s09": CapacityExceeded,
    "s10": CapacityExceeded,
    "s11": RoomConflict,
    "s12": OutsideOpeningHours,
}

BY_ID = pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)


def test_twelve_scenarios_seven_aligned_five_trap() -> None:
    assert len(SCENARIOS) == 12
    assert len({s.id for s in SCENARIOS}) == 12
    kinds = [s.kind for s in SCENARIOS]
    assert kinds.count("aligned") == 7
    assert kinds.count("trap") == 5


def test_every_scenario_has_an_intended_error() -> None:
    assert set(INTENDED_ERROR) == {s.id for s in SCENARIOS}


def test_styled_errors_are_spread() -> None:
    errors = list(INTENDED_ERROR.values())
    assert errors.count(RoomConflict) >= 3
    assert errors.count(CapacityExceeded) >= 2
    assert errors.count(OutsideOpeningHours) >= 2


def test_get_scenario() -> None:
    assert get_scenario("s05") is SCENARIOS[4]
    with pytest.raises(KeyError, match="s99"):
        get_scenario("s99")


@BY_ID
def test_obvious_first_attempt_hits_intended_error(scenario: Scenario) -> None:
    with pytest.raises(INTENDED_ERROR[scenario.id]):
        scenario.new_world().book(scenario.first_attempt)


@BY_ID
def test_known_solution_is_correct(scenario: Scenario) -> None:
    world = scenario.new_world()
    world.book(scenario.solution)
    assert check(world, scenario) is Outcome.CORRECT


@BY_ID
def test_untouched_world_has_no_booking(scenario: Scenario) -> None:
    assert check(scenario.new_world(), scenario) is Outcome.NO_BOOKING


@BY_ID
def test_new_world_is_fresh_each_time(scenario: Scenario) -> None:
    scenario.new_world().book(scenario.solution)
    assert scenario.new_world().active_bookings(created_by="agent") == []


@BY_ID
def test_brief_names_first_attempt_and_states_every_constraint(scenario: Scenario) -> None:
    brief, attempt, c = scenario.brief, scenario.first_attempt, scenario.constraints
    assert f"room {attempt.room_id} " in brief
    assert f"from {format_time(attempt.start)} to {format_time(attempt.end)}" in brief
    assert c.date.isoformat() in brief
    assert attempt.date == c.date
    earliest = format_time(c.earliest_start)
    assert f"{earliest} or later" in brief or f"not start before {earliest}" in brief
    assert f"by {format_time(c.latest_end)}" in brief
    assert f"{c.duration_minutes} minutes" in brief
    assert f"{c.attendees} people" in brief
    for feature in c.required_features:
        assert feature.value in brief


@BY_ID
def test_brief_uses_no_relative_dates(scenario: Scenario) -> None:
    relative = (
        r"\b(today|tomorrow|yesterday|next|this week|monday|tuesday|wednesday|thursday|friday)\b"
    )
    assert not re.search(relative, scenario.brief, re.IGNORECASE)


@BY_ID
def test_first_attempt_matches_brief_except_for_state(scenario: Scenario) -> None:
    attempt, c = scenario.first_attempt, scenario.constraints
    assert minutes(attempt.end) - minutes(attempt.start) == c.duration_minutes
    assert attempt.attendees == c.attendees
    assert c.earliest_start <= attempt.start
    assert attempt.end <= c.latest_end


def test_booking_domain_imports_nothing_external() -> None:
    package = Path(__file__).parents[1] / "src" / "second_audience" / "booking"
    allowed_roots = {"collections", "dataclasses", "datetime", "enum", "typing", "second_audience"}
    for name in ("world.py", "scenarios.py", "checker.py"):
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert roots <= allowed_roots, f"{name} imports {roots - allowed_roots}"
