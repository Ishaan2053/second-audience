"""Replay two recorded sessions side by side: control on the left, the challenger on the right.

The replay only reads the ledger. It shows real recorded behaviour, step by step, and says
so on screen; nothing is re-ordered or edited, and no model is called.
"""

import argparse
import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from second_audience.booking.checker import Outcome
from second_audience.booking.scenarios import get_scenario
from second_audience.config import get_settings
from second_audience.core.ledger import Ledger, SessionRecord, ToolCallRecord, make_engine
from second_audience.demo.pairs import Pair, newest_pair
from second_audience.operator.spec import CONTROL_KEY

BANNER = "Replay of recorded sessions"
_HIDDEN_ARGUMENTS = frozenset({"session_id"})
_MAX_VALUE_CHARS = 80
_MAX_RESPONSE_CHARS = 240


class ReplayError(ValueError):
    """Raised when the requested sessions cannot be replayed as a pair."""


def choose_pair(
    ledger: Ledger,
    *,
    scenario_id: str | None = None,
    model: str | None = None,
    session_ids: Sequence[str] = (),
) -> Pair:
    """Pick the named sessions, or the newest exposed pair for a scenario (and model)."""
    if session_ids:
        return _named_pair(ledger, session_ids)
    if scenario_id is None:
        raise ReplayError("give --scenario or --sessions")
    pair = newest_pair(ledger.exposed_sessions(scenario_id, model))
    if pair is None:
        narrowed = f" and model {model}" if model else ""
        raise ReplayError(f"no exposed pair (one session per arm) for {scenario_id}{narrowed}")
    return pair


def _named_pair(ledger: Ledger, session_ids: Sequence[str]) -> Pair:
    """Order two named sessions by arm, refusing pairs that are not comparable."""
    records = ledger.sessions_by_id(session_ids)
    missing = set(session_ids) - {r.session_id for r in records}
    if len(session_ids) != 2 or missing:
        raise ReplayError(f"need two known session IDs; unknown: {sorted(missing)}")
    control = [r for r in records if r.variant == CONTROL_KEY]
    challenger = [r for r in records if r.variant not in (None, CONTROL_KEY)]
    if len(control) != 1 or len(challenger) != 1:
        raise ReplayError("the sessions must be one control and one challenger exposure")
    if control[0].scenario_id != challenger[0].scenario_id:
        raise ReplayError("the sessions must be on the same scenario")
    if control[0].finished_at is None or challenger[0].finished_at is None:
        raise ReplayError("both sessions must be finished")
    return Pair(control[0], challenger[0])


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def format_arguments(arguments: dict[str, Any]) -> str:
    """The call's key arguments as `name=value`, without the session ID."""
    shown = (
        f"{key}={_clip(str(value), _MAX_VALUE_CHARS)}"
        for key, value in arguments.items()
        if key not in _HIDDEN_ARGUMENTS
    )
    return " ".join(shown) or "(no arguments)"


def format_response(result: Any) -> str:
    """A one-line reading of a tool response, specific for the booking tools' shapes."""
    if isinstance(result, dict):
        if "error" in result:
            error = result["error"]
            return f"ERROR {error['code']}: {error['message']}"
        if "booking" in result:
            b = result["booking"]
            return (
                f"booked {b['booking_id']}: {b['room_id']} {b['date']} "
                f"{b['start']}-{b['end']}, {b['attendees']} people ({b['status']})"
            )
        if "rooms" in result:
            rooms = ", ".join(
                f"{r['room_id']} ({r['capacity']}{''.join(', ' + f for f in r['features'])})"
                for r in result["rooms"]
            )
            return f"{len(result['rooms'])} rooms: {rooms}"
        if "finished" in result:
            return f"finished; scored outcome: {result['outcome']}"
        if "free_slots" in result:
            slots = ", ".join(f"{s['start']}-{s['end']}" for s in result["free_slots"]) or "none"
            return f"{result['room_id']} on {result['date']} free: {slots}"
    text = json.dumps(result, ensure_ascii=False, default=str)
    return _clip(text, _MAX_RESPONSE_CHARS)


def _suggestion_block(result: dict[str, Any]) -> Panel:
    """The suggestion (and corrected call, if any) the challenger's error carried."""
    s = result["suggestion"]
    lines = [f"{s['room_id']} {s['date']} {s['start']}-{s['end']}: {s['reason']}"]
    if "corrected_call" in result:
        call = result["corrected_call"]
        lines.append(f"corrected_call: {call['tool']} {format_arguments(call['arguments'])}")
    if "note" in result:
        lines.append(f"note: {result['note']}")
    return Panel("\n".join(lines), title="suggestion", border_style="yellow", title_align="left")


def _call_panel(call: ToolCallRecord, first_styled: bool) -> Panel:
    """One tool call: name and key arguments, then the response."""
    parts: list[RenderableType] = [
        Text(format_arguments(call.arguments), style="cyan"),
        Text("-> " + format_response(call.result), style="bold red" if call.error_code else ""),
    ]
    if call.result_truncated:
        parts.append(Text("(stored response was truncated)", style="dim"))
    if isinstance(call.result, dict) and "suggestion" in call.result:
        parts.append(_suggestion_block(call.result))
    return Panel(
        Group(*parts),
        title=f"#{call.seq} {call.tool}",
        title_align="left",
        subtitle="first styled error" if first_styled else None,
        subtitle_align="right",
        border_style="bold red" if first_styled else "blue",
    )


def _first_styled_seq(calls: Sequence[ToolCallRecord]) -> int | None:
    return next((c.seq for c in calls if c.styled), None)


def _two_columns(left: RenderableType, right: RenderableType) -> Table:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return grid


def _arm_title(record: SessionRecord) -> str:
    if record.variant == CONTROL_KEY:
        return "control: informative error"
    return f"{record.variant}: error with suggestion"


def _header(pair: Pair) -> RenderableType:
    """Banner, scenario, model and session IDs, then the brief once, full width."""
    control, challenger = pair.control, pair.challenger
    dates = sorted({r.started_at.date().isoformat() for r in (control, challenger)})
    banner = Text(f"{BANNER} (recorded {', '.join(dates)} UTC; no live model call)", style="bold")
    facts = Table.grid(padding=(0, 2))
    facts.add_row("scenario", f"{control.scenario_id} ({control.scenario_kind})")
    models = {control.agent_label, challenger.agent_label}
    facts.add_row("model", ", ".join(sorted(models)))
    facts.add_row("left", f"{_arm_title(control)}  session {control.session_id}")
    facts.add_row("right", f"{_arm_title(challenger)}  session {challenger.session_id}")
    brief = Panel(get_scenario(control.scenario_id).brief, title="brief", title_align="left")
    return Group(Panel(Group(banner, facts), border_style="magenta"), brief)


def _footer(pair: Pair) -> Table:
    """Each session's outcome and tool-call count."""

    def summary(record: SessionRecord) -> Panel:
        correct = record.outcome == Outcome.CORRECT
        return Panel(
            f"outcome: {record.outcome}\n"
            f"tool calls: {record.tool_call_count} (the metric; finish_task not counted)",
            title=_arm_title(record),
            title_align="left",
            border_style="green" if correct else "red",
        )

    return _two_columns(summary(pair.control), summary(pair.challenger))


def render_replay(
    console: Console,
    pair: Pair,
    transcripts: dict[str, list[ToolCallRecord]],
    pause: Callable[[], None],
) -> None:
    """Print the header, then each step side by side (calling `pause` between), then outcomes."""
    left = transcripts.get(pair.control.session_id, [])
    right = transcripts.get(pair.challenger.session_id, [])
    first_left, first_right = _first_styled_seq(left), _first_styled_seq(right)
    console.print(_header(pair))
    console.print(
        _two_columns(
            Text(_arm_title(pair.control), style="bold"),
            Text(_arm_title(pair.challenger), style="bold"),
        )
    )
    for step in range(max(len(left), len(right))):
        cells = [
            _call_panel(calls[step], calls[step].seq == first) if step < len(calls) else Text("")
            for calls, first in ((left, first_left), (right, first_right))
        ]
        console.print(_two_columns(*cells))
        pause()
    console.print(_footer(pair))
    console.print(Text(BANNER, style="dim"))


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point for `scripts/replay.py`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--scenario", help="scenario ID; replays the newest exposed pair")
    target.add_argument("--sessions", nargs=2, metavar=("ID_A", "ID_B"), help="two session IDs")
    parser.add_argument("--model", help="with --scenario: only sessions by this agent label")
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between steps")
    parser.add_argument("--step", action="store_true", help="wait for Enter between steps")
    parser.add_argument("--width", type=int, help="console width in columns (for recording)")
    args = parser.parse_args(argv)

    console = Console(width=args.width)
    ledger = Ledger(make_engine(get_settings(), read_only=True), create_schema=False)
    try:
        pair = choose_pair(
            ledger, scenario_id=args.scenario, model=args.model, session_ids=args.sessions or ()
        )
    except ReplayError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    transcripts = ledger.tool_calls_for([pair.control.session_id, pair.challenger.session_id])

    def pause() -> None:
        if args.step:
            console.input()
        else:
            time.sleep(args.delay)

    render_replay(console, pair, transcripts, pause)
    return 0
