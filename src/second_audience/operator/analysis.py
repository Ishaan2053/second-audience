"""Everything `sa status` and `sa report` compute, from Flagsmith results and ledger rows.

Pure functions over already-fetched data, so every number is reproducible from the saved
results JSON, `manual.yaml` and the ledger. Rules follow `HYPOTHESIS.md` section 10.
"""

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from second_audience.core.ledger import SessionRecord
from second_audience.operator.results import ExperimentResults, Inference, VariantStats
from second_audience.operator.spec import CONTROL_KEY, ExperimentSpec, Metric
from second_audience.operator.stats import (
    Interval,
    SrmTest,
    srm_test,
    stopping_rule_met,
    wilson,
)

SMALL_CELL = 20
"""Breakdown cells with fewer scored sessions than this are called out as small."""

ABANDONED = "abandoned"
CORRECT = "correct"
INCORRECT = "incorrect"
OUTCOMES = ("correct", "incorrect", "no_booking", "budget_exhausted", "abandoned")

FROM_API = "Flagsmith API"
FROM_DASHBOARD = "from dashboard"


class Decision(StrEnum):
    """What the pre-registered decision rule says to do."""

    SHIP = "ship"
    NO_SHIP = "do not ship"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class RunState:
    """What `sa start` recorded in `experiments/.state/<slug>.json`."""

    experiment_id: int
    environment_key: str
    metric_ids: dict[str, int]
    started_at: datetime
    commit: str

    @classmethod
    def load(cls, path: Path) -> "RunState":
        """Read the state file; raises `FileNotFoundError` if `sa start` never ran."""
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            experiment_id=int(raw["experiment_id"]),
            environment_key=str(raw["environment_key"]),
            metric_ids={str(k): int(v) for k, v in raw["metric_ids"].items()},
            started_at=datetime.fromisoformat(str(raw["started_at"]).replace("Z", "+00:00")),
            commit=str(raw["commit"]),
        )


# --- ledger predicates: these mirror what the server sends to Flagsmith (booking/sessions.py)


def in_experiment(record: SessionRecord) -> bool:
    """Exposed and enrolled: the sessions Flagsmith counts."""
    return record.exposed_at is not None and bool(record.in_experiment)


def scored(record: SessionRecord) -> bool:
    """Enrolled and finished but not abandoned: the sessions that sent `task_finished`."""
    return in_experiment(record) and record.outcome is not None and record.outcome != ABANDONED


def fallback(record: SessionRecord) -> bool:
    """Exposed but left out of the experiment: Flagsmith was unreachable at exposure."""
    return record.exposed_at is not None and not record.in_experiment


def event_value(record: SessionRecord, event: str) -> float:
    """What one scored session contributed to a metric's sum in Flagsmith."""
    if event == "task_finished":
        return float(record.tool_call_count)
    if event == "task_completed":
        return 1.0 if record.final_state == CORRECT else 0.0
    if event == "task_wrong_booking":
        return 1.0 if record.final_state == INCORRECT else 0.0
    raise ValueError(f"unknown event {event!r}")


# --- the headline ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricRow:
    """One metric's per-arm statistics and the variant's inference, with its source."""

    metric: Metric
    metric_id: int
    stats: dict[str, VariantStats | None]
    inference: Inference | None
    inference_source: str | None

    def credibly_worse(self) -> bool:
        """Whether the credible interval on lift lies entirely on the bad side of zero."""
        if self.inference is None:
            return False
        if self.metric.direction == "up":
            return self.inference.ci_high < 0
        return self.inference.ci_low > 0


def metric_rows(
    spec: ExperimentSpec,
    state: RunState,
    results: ExperimentResults,
    manual: dict[str, Inference],
) -> list[MetricRow]:
    """Primary metric first, then the others in spec order; API inference before manual."""
    variant = spec.variants[0].key
    ordered = [spec.primary_metric, *(m for m in spec.metrics if m.role != "primary")]
    rows = []
    for metric in ordered:
        metric_id = state.metric_ids[metric.name]
        found = results.metric(metric_id)
        stats = {arm.key: (found.variants.get(arm.key) if found else None) for arm in spec.arms}
        inference = found.inference.get(variant) if found else None
        source = FROM_API if inference is not None else None
        if inference is None and metric.name in manual:
            inference, source = manual[metric.name], FROM_DASHBOARD
        rows.append(MetricRow(metric, metric_id, stats, inference, source))
    return rows


def flagsmith_exposures(spec: ExperimentSpec, rows: list[MetricRow]) -> dict[str, int]:
    """Exposed identities per arm, as Flagsmith counts them (the primary metric's `n`)."""
    primary = rows[0]
    return {arm.key: (s.n if (s := primary.stats[arm.key]) else 0) for arm in spec.arms}


def ledger_exposures(spec: ExperimentSpec, records: Iterable[SessionRecord]) -> dict[str, int]:
    """Exposed, enrolled sessions per arm in the ledger."""
    counts = Counter(r.variant for r in records if in_experiment(r))
    return {arm.key: counts.get(arm.key, 0) for arm in spec.arms}


# --- decision -------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionResult:
    """The decision and each step of the rule that produced it."""

    decision: Decision
    rule_met: bool
    reasons: list[str]


def decide(
    spec: ExperimentSpec, rows: list[MetricRow], exposures: dict[str, int], now: datetime
) -> DecisionResult:
    """Apply the pre-registered rule (HYPOTHESIS.md section 10)."""
    rule = spec.stopping_rule
    arms = [arm.key for arm in spec.arms]
    met = stopping_rule_met(exposures, arms, rule.min_exposed_per_arm, rule.hard_end, now)
    counts = ", ".join(f"{arm} {exposures[arm]}" for arm in arms)
    if not met:
        return DecisionResult(
            Decision.INCONCLUSIVE,
            False,
            [
                f"Stopping rule not met: exposed sessions per arm are {counts}, below "
                f"{rule.min_exposed_per_arm}, and the hard end {rule.hard_end.isoformat()} "
                "has not passed. No winner is named before then."
            ],
        )
    reasons = [f"Stopping rule met: exposed sessions per arm are {counts}."]
    primary, guardrails = rows[0], [r for r in rows if r.metric.role == "guardrail"]
    threshold = rule.win_probability_threshold
    if primary.inference is None:
        reasons.append(
            f"Flagsmith shows no inference on {primary.metric.name!r} yet, so the rule "
            "cannot be applied."
        )
        return DecisionResult(Decision.INCONCLUSIVE, True, reasons)
    chance = primary.inference.chance_to_win
    reasons.append(
        f"Win probability of {spec.variants[0].key} on {primary.metric.name!r} is "
        f"{chance:.3f} against a threshold of {threshold:g}"
        f" ({'met' if chance >= threshold else 'not met'})."
    )
    worse = [r.metric.name for r in guardrails if r.credibly_worse()]
    for row in guardrails:
        if row.inference is None:
            reasons.append(
                f"Guardrail {row.metric.name!r}: no inference from Flagsmith; ledger counts "
                "are reported instead."
            )
        else:
            verdict = "credibly worse" if row.credibly_worse() else "not credibly worse"
            reasons.append(
                f"Guardrail {row.metric.name!r}: lift interval [{row.inference.ci_low:.3f}, "
                f"{row.inference.ci_high:.3f}], {verdict}."
            )
    if chance >= threshold and not worse:
        return DecisionResult(Decision.SHIP, True, reasons)
    return DecisionResult(Decision.NO_SHIP, True, reasons)


# --- reconciliation -------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconRow:
    """Ledger against Flagsmith for one measure and one arm."""

    measure: str
    arm: str
    ledger: float
    ledger_at_as_of: float | None
    flagsmith: float | None

    @property
    def difference(self) -> float | None:
        """Ledger minus Flagsmith."""
        return None if self.flagsmith is None else self.ledger - self.flagsmith

    @property
    def cause(self) -> str:
        """The likely cause of any difference, from the timestamps."""
        if self.flagsmith is None or self.ledger_at_as_of is None:
            return "no Flagsmith results yet"
        if abs(self.ledger - self.flagsmith) < 1e-9:
            return "match"
        if abs(self.ledger_at_as_of - self.flagsmith) < 1e-9:
            return "event latency: recorded after Flagsmith's as_of"
        late = self.ledger - self.ledger_at_as_of
        gap = self.ledger_at_as_of - self.flagsmith
        return (
            f"{_num(late)} after as_of; {_num(gap)} not explained by timing "
            "(events lost or still buffered)"
        )


def reconcile(
    spec: ExperimentSpec,
    rows: list[MetricRow],
    records: list[SessionRecord],
    as_of: datetime | None,
) -> list[ReconRow]:
    """Exposures and each metric's sum, per arm, in the ledger and in Flagsmith."""

    def by(cutoff: Callable[[SessionRecord], datetime | None]) -> Callable[[SessionRecord], bool]:
        def keep(record: SessionRecord) -> bool:
            at = cutoff(record)
            return as_of is not None and at is not None and at <= as_of

        return keep

    exposed_by_as_of = by(lambda r: r.exposed_at)
    finished_by_as_of = by(lambda r: r.finished_at)
    out = []
    for arm in spec.arms:
        mine = [r for r in records if r.variant == arm.key]
        exposed = [r for r in mine if in_experiment(r)]
        stats = rows[0].stats[arm.key]
        out.append(
            ReconRow(
                "Exposed sessions",
                arm.key,
                len(exposed),
                len([r for r in exposed if exposed_by_as_of(r)]) if as_of else None,
                stats.n if stats else None,
            )
        )
        done = [r for r in mine if scored(r)]
        for row in rows:
            event = row.metric.event
            metric_stats = row.stats[arm.key]
            out.append(
                ReconRow(
                    f"{event} (sum)",
                    arm.key,
                    sum(event_value(r, event) for r in done),
                    sum(event_value(r, event) for r in done if finished_by_as_of(r))
                    if as_of
                    else None,
                    metric_stats.sum if metric_stats else None,
                )
            )
    return out


# --- breakdowns and traffic -----------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One breakdown cell: scored, enrolled sessions of one group in one arm."""

    group: str
    arm: str
    n: int
    correct: Interval | None
    wrong: Interval | None
    mean_tool_calls: float | None


def breakdown(
    spec: ExperimentSpec, records: list[SessionRecord], key: Callable[[SessionRecord], str]
) -> list[Cell]:
    """Per group and arm: n, correct and wrong-booking rates (Wilson), mean tool calls."""
    done = [r for r in records if scored(r)]
    cells = []
    for group in sorted({key(r) for r in done}):
        for arm in spec.arms:
            rows = [r for r in done if key(r) == group and r.variant == arm.key]
            n = len(rows)
            cells.append(
                Cell(
                    group,
                    arm.key,
                    n,
                    wilson(sum(r.final_state == CORRECT for r in rows), n),
                    wilson(sum(r.final_state == INCORRECT for r in rows), n),
                    sum(r.tool_call_count for r in rows) / n if n else None,
                )
            )
    return cells


BREAKDOWNS: dict[str, Callable[[SessionRecord], str]] = {
    "All exposed sessions": lambda r: "all",
    "By scenario kind": lambda r: r.scenario_kind,
    "By model": lambda r: r.agent_label,
    "By source": lambda r: r.source,
}


@dataclass(frozen=True)
class TrafficRow:
    """Sessions of one source and model since the experiment started."""

    source: str
    model: str
    sessions: int
    exposed: int
    enrolled: int
    fallback: int
    outcomes: dict[str, int]

    @property
    def share_exposed(self) -> float:
        """Exposed sessions over all sessions."""
        return self.exposed / self.sessions if self.sessions else 0.0


def traffic(records: list[SessionRecord]) -> list[TrafficRow]:
    """Sessions, exposure and outcomes by source and model; `open` means not yet finished."""
    rows = []
    for source, model in sorted({(r.source, r.agent_label) for r in records}):
        mine = [r for r in records if (r.source, r.agent_label) == (source, model)]
        outcomes = Counter(r.outcome or "open" for r in mine)
        rows.append(
            TrafficRow(
                source,
                model,
                len(mine),
                sum(r.exposed_at is not None for r in mine),
                sum(in_experiment(r) for r in mine),
                sum(fallback(r) for r in mine),
                {name: outcomes.get(name, 0) for name in (*OUTCOMES, "open")},
            )
        )
    return rows


def trap_wrong_bookings(variant: str, records: list[SessionRecord]) -> list[SessionRecord]:
    """Scored wrong bookings by the variant arm on trap scenarios."""
    return [
        r
        for r in records
        if scored(r)
        and r.variant == variant
        and r.scenario_kind == "trap"
        and r.final_state == INCORRECT
    ]


# --- the whole analysis ---------------------------------------------------------------------


@dataclass(frozen=True)
class Analysis:
    """Every computed input of the report."""

    spec: ExperimentSpec
    state: RunState
    results: ExperimentResults
    rows: list[MetricRow]
    records: list[SessionRecord]
    exposures: dict[str, int]
    decision: DecisionResult
    reconciliation: list[ReconRow]
    srm: SrmTest | None
    breakdowns: dict[str, list[Cell]]
    traffic: list[TrafficRow]
    trap_wrong: list[SessionRecord]
    now: datetime
    small_cells: list[str] = field(default_factory=list)


def analyse(
    spec: ExperimentSpec,
    state: RunState,
    results: ExperimentResults,
    manual: dict[str, Inference],
    records: list[SessionRecord],
    now: datetime,
) -> Analysis:
    """Compute every section's numbers. Two-arm experiments only."""
    if len(spec.arms) != 2 or spec.control.key != CONTROL_KEY:
        raise ValueError("sa report supports two-arm experiments (control and one variant)")
    rows = metric_rows(spec, state, results, manual)
    exposures = flagsmith_exposures(spec, rows)
    breakdowns = {title: breakdown(spec, records, key) for title, key in BREAKDOWNS.items()}
    small = [
        f"{title.lower()}: {cell.group} / {cell.arm} (n = {cell.n})"
        for title, cells in breakdowns.items()
        for cell in cells
        if cell.n < SMALL_CELL
    ]
    return Analysis(
        spec=spec,
        state=state,
        results=results,
        rows=rows,
        records=records,
        exposures=exposures,
        decision=decide(spec, rows, exposures, now),
        reconciliation=reconcile(spec, rows, records, results.as_of),
        srm=srm_test(exposures, spec.allocation.split),
        breakdowns=breakdowns,
        traffic=traffic(records),
        trap_wrong=trap_wrong_bookings(spec.variants[0].key, records),
        now=now,
        small_cells=small,
    )


def _num(value: float) -> str:
    """An integer without a decimal point, otherwise two decimals."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"
