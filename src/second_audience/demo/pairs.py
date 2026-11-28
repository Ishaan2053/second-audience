"""Pairs of recorded sessions on the same scenario and model, one per arm, with honest counts.

The population is what `sa report` analyses: scored sessions (enrolled, finished, not
abandoned) of one traffic source, started since the experiment's start, without `sa ship`
smoke runs. Fallback sessions (Flagsmith unreachable at exposure) have no arm, so they are
never in the population or in its counts. A cell is one source, scenario and agent label.
Every control session in a cell is paired with every challenger session in it, so one
session can appear in several pairs.
Pairs are for choosing a replay; the session-level counts are what a claim should cite.
"""

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import get_args

from rich.console import Console
from rich.table import Table
from rich.text import Text
from sqlalchemy.exc import SQLAlchemyError

from second_audience.booking.checker import Outcome
from second_audience.config import get_settings
from second_audience.core.ledger import Ledger, SessionRecord, Source, make_engine
from second_audience.operator.analysis import ABANDONED, RunState, scored, ship_smoke
from second_audience.operator.spec import CONTROL_KEY
from second_audience.operator.start import state_path

DEFAULT_SLUG = "error-style-v1"
SOURCES: tuple[str, ...] = get_args(Source)
_EXPERIMENTS = Path("experiments")


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


def experiment_start(slug: str) -> datetime:
    """When `sa start` started the experiment, from `experiments/.state/<slug>.json`."""
    return RunState.load(state_path(_EXPERIMENTS / f"{slug}.yaml", slug)).started_at


def population(records: Iterable[SessionRecord], source: str) -> list[SessionRecord]:
    """The sessions `sa report` analyses, from one source: scored and not a smoke run."""
    return [r for r in records if r.source == source and scored(r) and not ship_smoke(r)]


def _cell(record: SessionRecord) -> Cell:
    return Cell(record.source, record.scenario_id, record.scenario_kind, record.agent_label)


def by_cell(
    records: Iterable[SessionRecord],
) -> dict[Cell, tuple[list[SessionRecord], list[SessionRecord]]]:
    """Group a `population` into cells of (control, challenger) lists."""
    cells: dict[Cell, tuple[list[SessionRecord], list[SessionRecord]]] = defaultdict(
        lambda: ([], [])
    )
    for record in records:
        control, challenger = cells[_cell(record)]
        (control if record.variant == CONTROL_KEY else challenger).append(record)
    return dict(sorted(cells.items(), key=lambda item: _cell_key(item[0])))


def _cell_key(cell: Cell) -> tuple[str, str, str]:
    return cell.source, cell.scenario_id, cell.agent_label


def pairs_in(control: list[SessionRecord], challenger: list[SessionRecord]) -> list[Pair]:
    """Every control x challenger combination in one cell."""
    return [Pair(c, p) for c in control for p in challenger]


def all_pairs(records: Iterable[SessionRecord]) -> list[Pair]:
    """Every pair across all cells of a `population`."""
    return [pair for c, p in by_cell(records).values() for pair in pairs_in(c, p)]


def newest_pair(records: Iterable[SessionRecord]) -> Pair | None:
    """The pair whose later session started most recently, or None if no cell has both arms."""
    pairs = all_pairs(records)
    return max(pairs, key=lambda pair: pair.recency) if pairs else None


PatternKey = tuple[str, str, OutcomePattern, CallsPattern]


def pattern_counts(pairs: Iterable[Pair]) -> Counter[PatternKey]:
    """Count pairs by scenario kind, challenger variant, outcome pattern and calls pattern."""
    return Counter(
        (
            pair.control.scenario_kind,
            str(pair.challenger.variant),
            pair.outcome_pattern,
            pair.calls_pattern,
        )
        for pair in pairs
    )


def session_outcomes(records: Iterable[SessionRecord]) -> Counter[tuple[str, str, str]]:
    """Count a `population` by scenario kind, arm (variant key) and outcome."""
    return Counter((r.scenario_kind, str(r.variant), str(r.outcome)) for r in records)


def _scope(source: str, since: datetime) -> str:
    return f"{source} sessions since {since:%Y-%m-%d %H:%M} UTC"


def _session_table(exposed: list[SessionRecord], counted: list[SessionRecord], scope: str) -> Table:
    """Scored sessions per kind and arm: outcomes, mean tool calls, and the abandoned ones."""
    table = Table(title=f"{scope}: scored exposed sessions by kind and arm (cite these counts)")
    columns = ("kind", "arm", "sessions", "outcomes", "mean tool calls", "abandoned")
    for column in columns:
        table.add_column(column)
    outcomes = session_outcomes(counted)
    groups: dict[tuple[str, str], list[SessionRecord]] = defaultdict(list)
    for r in counted:
        groups[(r.scenario_kind, str(r.variant))].append(r)
    left_out = Counter((r.scenario_kind, str(r.variant)) for r in exposed if r.outcome == ABANDONED)
    for kind, arm in sorted(groups.keys() | left_out.keys()):
        group = groups.get((kind, arm), [])
        breakdown = ", ".join(
            f"{outcome} {n}"
            for (k, a, outcome), n in sorted(outcomes.items())
            if (k, a) == (kind, arm)
        )
        mean = f"{sum(r.tool_call_count for r in group) / len(group):.2f}" if group else "-"
        table.add_row(kind, arm, str(len(group)), breakdown, mean, str(left_out[(kind, arm)]))
    return table


def _pattern_table(pairs: list[Pair], scope: str) -> Table:
    """Pair counts per kind, challenger, outcome and calls pattern, with each group's total."""
    table = Table(title=f"{scope}: pair patterns (every pattern, favourable or not)")
    columns = ("kind", "challenger", "outcome pattern", "calls pattern", "pairs", "of group")
    for column in columns:
        table.add_column(column)
    totals = Counter((pair.control.scenario_kind, str(pair.challenger.variant)) for pair in pairs)
    for (kind, variant, outcome, calls), n in sorted(pattern_counts(pairs).items()):
        table.add_row(kind, variant, outcome, calls, str(n), str(totals[(kind, variant)]))
    return table


def _pair_table(
    cells: dict[Cell, tuple[list[SessionRecord], list[SessionRecord]]], scope: str
) -> Table:
    """Every pair, with the cells that lack one arm listed too."""
    table = Table(title=f"{scope}: candidate pairs (every control x challenger per cell)")
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
                f"{pair.challenger.variant} {_describe(pair.challenger)}",
                "OUTCOME DIFFERS" if differs else "",
                style="bold" if differs else None,
            )
    return table


def _describe(record: SessionRecord) -> str:
    return f"{record.session_id} {record.outcome} {record.tool_call_count} calls"


def report(console: Console, exposed: list[SessionRecord], source: str, since: datetime) -> None:
    """Print session counts, pair pattern counts and every candidate pair for one source."""
    mine = [r for r in exposed if r.source == source and not ship_smoke(r)]
    counted = population(mine, source)
    scope = _scope(source, since)
    cells = by_cell(counted)
    pairs = [pair for c, p in cells.values() for pair in pairs_in(c, p)]
    console.print(_session_table(mine, counted, scope))
    console.print(_pattern_table(pairs, scope))
    console.print(_pair_table(cells, scope))
    console.print(
        "Counts cover scored sessions (enrolled, finished, not abandoned), as `sa report` "
        "does; 'abandoned' counts the exposed sessions left out for that reason. Fallback "
        "sessions (no arm) are not counted anywhere.\n"
        "'correct' here is the session's outcome label; `sa report`'s task_completed scores "
        "the final state, so the two can differ (for example a budget_exhausted session whose "
        "final state is correct).\n"
        "Pairs reuse sessions (each control x challenger combination in a cell), so cite the "
        "session counts above, not pair counts, when describing how typical a replay is.\n"
        "Replay a pair with: uv run python scripts/replay.py --sessions <control> <challenger>"
    )


def open_ledger() -> Ledger:
    """The ledger from settings, opened read-only: the database refuses writes."""
    return Ledger(make_engine(get_settings(), read_only=True), create_schema=False)


def guarded(console: Console, body: Callable[[], int]) -> int:
    """Run a script body; a missing run state, unreadable ledger or closed input exits 2."""
    try:
        return body()
    except FileNotFoundError as exc:
        message = f"{exc.filename} not found; has `sa start` run for this slug?"
    except SQLAlchemyError as exc:
        message = f"cannot read the ledger: {str(exc).splitlines()[0]}"
    except EOFError:
        message = "input ended; stopped"
    console.print(Text(message, style="red"))
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point for `scripts/find_pairs.py`."""
    parser = argparse.ArgumentParser(description="List replay candidates with honest counts.")
    parser.add_argument("--source", choices=SOURCES, default="lab", help="traffic source")
    parser.add_argument("--slug", default=DEFAULT_SLUG, help="experiment whose window to use")
    parser.add_argument("--scenario", help="only this scenario ID")
    parser.add_argument("--model", help="only sessions by this agent label")
    parser.add_argument("--width", type=int, help="console width in columns")
    args = parser.parse_args(argv)
    console = Console(width=args.width)

    def body() -> int:
        since = experiment_start(args.slug)
        exposed = open_ledger().exposed_sessions(args.scenario, args.model, since)
        report(console, exposed, args.source, since)
        return 0

    return guarded(console, body)
