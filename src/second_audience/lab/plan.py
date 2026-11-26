"""The lab run plan: models x scenarios x replicates, shuffled with a recorded seed."""

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from second_audience.booking.scenarios import SCENARIOS
from second_audience.core.ledger import Ledger, PlannedRun

PLAN_AUDIT_ACTION = "lab_plan"


@dataclass(frozen=True, slots=True)
class PlanResult:
    """What one `plan` call added."""

    added: int
    seed: int
    replicates: dict[str, tuple[int, int]]
    """Per model, the first and last replicate number added."""


def build_plan(
    models: Sequence[str],
    scenario_ids: Sequence[str],
    replicates: int,
    existing: Mapping[str, int],
    seed: int,
) -> list[PlannedRun]:
    """Rows for `replicates` more replicates per model after `existing`, in shuffled order."""
    if replicates < 1:
        raise ValueError("replicates must be at least 1")
    rows = [
        PlannedRun(model, scenario_id, replicate)
        for model in models
        for replicate in range(existing.get(model, 0) + 1, existing.get(model, 0) + replicates + 1)
        for scenario_id in scenario_ids
    ]
    random.Random(seed).shuffle(rows)
    return rows


def plan_runs(ledger: Ledger, models: Sequence[str], replicates: int, seed: int) -> PlanResult:
    """Add `replicates` replicates of every scenario per model and record the seed."""
    existing = ledger.max_replicates()
    rows = build_plan(models, [s.id for s in SCENARIOS], replicates, existing, seed)
    added = ledger.plan_runs(rows)
    ranges = {
        model: (existing.get(model, 0) + 1, existing.get(model, 0) + replicates) for model in models
    }
    ledger.audit(
        PLAN_AUDIT_ACTION,
        None,
        json.dumps({"seed": seed, "replicates": replicates, "added": added, "ranges": ranges}),
        None,
    )
    return PlanResult(added, seed, ranges)
