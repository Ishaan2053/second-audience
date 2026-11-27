"""Pairs of recorded sessions on the same scenario and model, one per arm, with honest counts.

A cell is one source, scenario and agent label. Every control session in a cell is paired
with every challenger session in it, so one session can appear in several pairs. Pairs are
for choosing a replay; the session-level counts are what a claim about the demo should cite.
"""

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rich.console import Console
from rich.table import Table

from second_audience.booking.checker import Outcome
from second_audience.config import get_settings
from second_audience.core.ledger import Ledger, SessionRecord, make_engine
from second_audience.operator.analysis import ship_smoke
from second_audience.operator.spec import CONTROL_KEY


class OutcomePattern(StrEnum):
    """How the two outcomes of a pair compare."""

    SAME = "same outcome"
    ONLY_CONTROL_CORRECT = "only control correct"
    ONLY_CHALLENGER_CORRECT = "only challenger correct"
    DIFFERENT_NEITHER_CORRECT = "different, neither correct"


class CallsPattern(StrEnum):
    """How the challenger's tool-call count compares with the control's."""

    FEWER = "challenger fewer calls"
    EQUAL = "equal calls"
    MORE = "challenger more calls"


@dataclass(frozen=True, slots=True)
class Cell:
    """The grouping a pair shares: traffic source, scenario and agent label (the model)."""

    source: str
    scenario_id: str
    scenario_kind: str
    agent_label: str


@dataclass(frozen=True, slots=True)
class Pair:
    """One control session and one challenger session from the same cell."""

    control: SessionRecord
    challenger: SessionRecord

    @property
    def outcome_pattern(self) -> OutcomePattern:
        """Compare the two outcomes."""
        left, right = self.control.outcome, self.challenger.outcome
        if left == right:
            return OutcomePattern.SAME
        if left == Outcome.CORRECT:
            return OutcomePattern.ONLY_CONTROL_CORRECT
        if right == Outcome.CORRECT:
            return OutcomePattern.ONLY_CHALLENGER_CORRECT
        return OutcomePattern.DIFFERENT_NEITHER_CORRECT

    @property
    def calls_pattern(self) -> CallsPattern:
        """Compare the two tool-call counts."""
        diff = self.challenger.tool_call_count - self.control.tool_call_count
        if diff < 0:
            return CallsPattern.FEWER
        return CallsPattern.EQUAL if diff == 0 else CallsPattern.MORE

    @property
    def recency(self) -> tuple[datetime, str, str]:
        """Sort key: the later of the two start times, then the session IDs."""
        latest = max(self.control.started_at, self.challenger.started_at)
        return latest, self.control.session_id, self.challenger.session_id


def _cell(record: SessionRecord) -> Cell:
    return Cell(record.source, record.scenario_id, record.scenario_kind, record.agent_label)


def by_cell(
    records: Iterable[SessionRecord],
) -> dict[Cell, tuple[list[SessionRecord], list[SessionRecord]]]:
    """Group exposed sessions into cells of (control, challenger) lists; smoke runs excluded."""
    cells: dict[Cell, tuple[list[SessionRecord], list[SessionRecord]]] = defaultdict(
        lambda: ([], [])
    )
    for record in records:
        if record.variant is None or ship_smoke(record):
            continue
        control, challenger = cells[_cell(record)]
        (control if record.variant == CONTROL_KEY else challenger).append(record)
    return dict(sorted(cells.items(), key=lambda item: _cell_key(item[0])))


def _cell_key(cell: Cell) -> tuple[str, str, str]:
    return cell.source, cell.scenario_id, cell.agent_label


def pairs_in(control: list[SessionRecord], challenger: list[SessionRecord]) -> list[Pair]:
    """Every control x challenger combination in one cell."""
    return [Pair(c, p) for c in control for p in challenger]


def all_pairs(records: Iterable[SessionRecord]) -> list[Pair]:
    """Every pair across all cells."""
    return [pair for c, p in by_cell(records).values() for pair in pairs_in(c, p)]


def newest_pair(records: Iterable[SessionRecord]) -> Pair | None:
    """The pair whose later session started most recently, or None if no cell has both arms."""
    pairs = all_pairs(records)
    return max(pairs, key=lambda pair: pair.recency) if pairs else None


def pattern_counts(pairs: Iterable[Pair]) -> Counter[tuple[str, OutcomePattern, CallsPattern]]:
    """Count pairs by scenario kind, outcome pattern and calls pattern."""
    return Counter(
        (pair.control.scenario_kind, pair.outcome_pattern, pair.calls_pattern) for pair in pairs
    )


def session_outcomes(records: Iterable[SessionRecord]) -> Counter[tuple[str, str, str]]:
    """Count exposed sessions by scenario kind, arm and outcome; smoke runs excluded."""
    return Counter(
        (r.scenario_kind, r.variant, r.outcome or "unfinished")
        for r in records
        if r.variant is not None and not ship_smoke(r)
    )


def _session_table(records: list[SessionRecord]) -> Table:
    """Session-level counts per scenario kind and arm: outcomes and mean tool calls."""
    table = Table(title="Exposed sessions by kind and arm (cite these counts)")
    for column in ("kind", "arm", "sessions", "outcomes", "mean tool calls"):
        table.add_column(column)
    outcomes = session_outcomes(records)
    groups: dict[tuple[str, str], list[SessionRecord]] = defaultdict(list)
    for r in records:
        if r.variant is not None and not ship_smoke(r):
            groups[(r.scenario_kind, r.variant)].append(r)
    for (kind, arm), group in sorted(groups.items()):
        breakdown = ", ".join(
            f"{outcome} {n}"
            for (k, a, outcome), n in sorted(outcomes.items())
            if (k, a) == (kind, arm)
        )
        mean = sum(r.tool_call_count for r in group) / len(group)
        table.add_row(kind, arm, str(len(group)), breakdown, f"{mean:.2f}")
    return table


def _pattern_table(pairs: list[Pair]) -> Table:
    """Pair counts per kind, outcome pattern and calls pattern, with each kind's total."""
    table = Table(title="Pair patterns (every pattern, favourable or not)")
    for column in ("kind", "outcome pattern", "calls pattern", "pairs", "of kind"):
        table.add_column(column)
    totals = Counter(pair.control.scenario_kind for pair in pairs)
    for (kind, outcome, calls), n in sorted(pattern_counts(pairs).items()):
        table.add_row(kind, outcome, calls, str(n), str(totals[kind]))
    return table


def _pair_table(cells: dict[Cell, tuple[list[SessionRecord], list[SessionRecord]]]) -> Table:
    """Every pair, with the cells that lack one arm listed too."""
    table = Table(title="Candidate pairs (all control x challenger combinations per cell)")
    for column in ("source", "scenario", "kind", "model", "control", "challenger", "flag"):
        table.add_column(column)
    for cell, (control, challenger) in cells.items():
        head = (cell.source, cell.scenario_id, cell.scenario_kind, cell.agent_label)
        if not (control and challenger):
            arm = "control" if control else "challenger"
            table.add_row(*head, "-", "-", f"only {arm} sessions; no pair", style="dim")
            continue
        for pair in pairs_in(control, challenger):
            differs = pair.outcome_pattern is not OutcomePattern.SAME
            table.add_row(
                *head,
                _describe(pair.control),
                _describe(pair.challenger),
                "OUTCOME DIFFERS" if differs else "",
                style="bold" if differs else None,
            )
    return table


def _describe(record: SessionRecord) -> str:
    return f"{record.session_id} {record.outcome} {record.tool_call_count} calls"


def report(console: Console, records: list[SessionRecord]) -> None:
    """Print session counts, pair pattern counts and every candidate pair."""
    cells = by_cell(records)
    pairs = [pair for c, p in cells.values() for pair in pairs_in(c, p)]
    console.print(_session_table(records))
    console.print(_pattern_table(pairs))
    console.print(_pair_table(cells))
    console.print(
        "Pairs reuse sessions (each control x challenger combination in a cell), so cite the "
        "session counts above, not pair counts, when describing how typical a replay is.\n"
        "Replay a pair with: uv run python scripts/replay.py --sessions <control> <challenger>"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point for `scripts/find_pairs.py`."""
    parser = argparse.ArgumentParser(description="List replay candidates with honest counts.")
    parser.add_argument("--scenario", help="only this scenario ID")
    parser.add_argument("--model", help="only sessions by this agent label")
    parser.add_argument("--width", type=int, help="console width in columns")
    args = parser.parse_args(argv)
    ledger = Ledger(make_engine(get_settings(), read_only=True), create_schema=False)
    report(Console(width=args.width), ledger.exposed_sessions(args.scenario, args.model))
    return 0
