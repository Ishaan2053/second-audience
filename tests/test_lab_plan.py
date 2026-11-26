"""The lab run plan: shuffled with a recorded seed, extended without duplicates."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from second_audience.booking.scenarios import SCENARIOS
from second_audience.core.ledger import Ledger, audit_log, lab_runs
from second_audience.lab.plan import PLAN_AUDIT_ACTION, build_plan, plan_runs


def test_build_plan_is_a_seeded_shuffle_of_every_cell() -> None:
    rows = build_plan(["m1", "m2"], ["s01", "s02"], 2, {"m2": 3}, seed=7)
    assert sorted((r.model, r.scenario_id, r.replicate) for r in rows) == [
        ("m1", "s01", 1),
        ("m1", "s01", 2),
        ("m1", "s02", 1),
        ("m1", "s02", 2),
        ("m2", "s01", 4),
        ("m2", "s01", 5),
        ("m2", "s02", 4),
        ("m2", "s02", 5),
    ]
    assert rows == build_plan(["m1", "m2"], ["s01", "s02"], 2, {"m2": 3}, seed=7)
    assert rows != build_plan(["m1", "m2"], ["s01", "s02"], 2, {"m2": 3}, seed=8)


def test_build_plan_needs_a_replicate() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        build_plan(["m1"], ["s01"], 0, {}, seed=1)


def test_plan_again_adds_replicates_and_records_the_seed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'plan.db'}")
    ledger = Ledger(engine)
    first = plan_runs(ledger, ["m1", "m2"], 2, seed=11)
    second = plan_runs(ledger, ["m1"], 1, seed=12)

    assert first.added == 2 * 2 * len(SCENARIOS)
    assert second.added == len(SCENARIOS)
    assert second.replicates == {"m1": (3, 3)}
    assert ledger.run_counts() == {
        "m1": {"planned": 3 * len(SCENARIOS)},
        "m2": {"planned": 2 * len(SCENARIOS)},
    }
    with engine.connect() as conn:
        keys = [(r.model, r.scenario_id, r.replicate) for r in conn.execute(select(lab_runs))]
        audits = list(conn.execute(select(audit_log)))
    assert len(keys) == len(set(keys))
    assert [a.action for a in audits] == [PLAN_AUDIT_ACTION] * 2
    assert json.loads(audits[0].detail)["seed"] == 11
    engine.dispose()
