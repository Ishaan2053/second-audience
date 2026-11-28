"""Demo kit: side-by-side replay and pair finding against a seeded SQLite ledger."""

import io
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import SecretStr
from rich.console import Console
from sqlalchemy import insert
from sqlalchemy.exc import OperationalError

from second_audience.booking.scenarios import get_scenario
from second_audience.config import Settings
from second_audience.core.ledger import Ledger, make_engine, sessions
from second_audience.demo import pairs, replay
from second_audience.demo.pairs import (
    CallsPattern,
    OutcomePattern,
    all_pairs,
    pattern_counts,
    population,
    session_outcomes,
)
from second_audience.demo.replay import BANNER, ReplayError, choose_pair, render_replay
from second_audience.operator.analysis import SMOKE_AGENT_LABEL

MODEL = "gemini-test"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
CONFLICT = {"code": "ROOM_CONFLICT", "message": "Room R3 is already booked 11:00-12:00."}
SUGGESTION = {
    "room_id": "R3",
    "date": "2027-03-18",
    "start": "12:00",
    "end": "13:00",
    "reason": "nearest free slot of the same length",
}
BOOKING = {
    "booking": {
        "booking_id": "B002",
        "room_id": "R3",
        "date": "2027-03-18",
        "start": "10:00",
        "end": "11:00",
        "attendees": 4,
        "status": "active",
    }
}


def _settings(path: Path) -> Settings:
    return Settings(sa_database_url=SecretStr(f"sqlite:///{path.as_posix()}"))


def _session(
    ledger: Ledger,
    session_id: str,
    scenario_id: str,
    variant: str | None,
    calls: list[tuple[str, dict[str, Any], Any, bool]],
    outcome: str = "correct",
    agent_label: str = MODEL,
    source: Literal["lab", "field"] = "lab",
    enrolled: bool = True,
) -> None:
    """Record a finished session; each call is (tool, arguments, result, styled)."""
    kind = get_scenario(scenario_id).kind
    ledger.start_session(session_id, scenario_id, kind, source, agent_label, None)
    for seq, (tool, arguments, result, styled) in enumerate(calls, start=1):
        if styled:  # a fallback, like production, has no variant and is not enrolled
            ledger.record_exposure(session_id, variant if enrolled else None, enrolled)
        code = result["error"]["code"] if isinstance(result, dict) and "error" in result else None
        args = {"session_id": session_id, **arguments}
        ledger.record_tool_call(session_id, seq, tool, args, result, code, styled)
    ledger.finish_session(session_id, outcome, len(calls), final_state=outcome)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A ledger file with a trap pair, an aligned pair whose outcomes differ, and noise.

    The noise: an unexposed pilot, a smoke run, a single-arm cell, an abandoned session, a
    fallback (exposed, not enrolled) session, a field pair, and a lab session by another model.
    """
    path = tmp_path / "ledger.db"
    ledger = Ledger(make_engine(_settings(path)))
    attempt = {"room_id": "R3", "date": "2027-03-18", "start": "11:00", "end": "12:00"}
    rooms = {"rooms": [{"room_id": "R3", "capacity": 6, "features": ["projector"]}]}
    _session(
        ledger,
        "ctrl-s05",
        "s05",
        "control",
        [
            ("list_rooms", {}, rooms, False),
            ("book_room", attempt, {"error": CONFLICT}, True),
            ("book_room", {"room_id": "R3", "start": "10:00"}, BOOKING, False),
        ],
    )
    corrected = {
        "tool": "book_room",
        "arguments": {"session_id": "presc-s05", **attempt, "start": "12:00", "end": "13:00"},
    }
    _session(
        ledger,
        "presc-s05",
        "s05",
        "prescriptive",
        [
            (
                "book_room",
                attempt,
                {"error": CONFLICT, "suggestion": SUGGESTION, "corrected_call": corrected},
                True,
            ),
            ("book_room", {"room_id": "R3", "start": "10:00"}, BOOKING, False),
        ],
    )
    _session(ledger, "ctrl-s08", "s08", "control", [("book_room", attempt, {}, True)] * 3)
    conflict = ("book_room", attempt, {"error": CONFLICT}, True)
    _session(ledger, "presc-s08", "s08", "prescriptive", [conflict], outcome="no_booking")
    _session(ledger, "pilot", "s05", None, [("list_rooms", {}, rooms, False)])
    _session(ledger, "smoke", "s05", "prescriptive", [], agent_label=SMOKE_AGENT_LABEL)
    _session(ledger, "lonely", "s01", "control", [("book_room", attempt, {}, True)])
    _session(ledger, "gave-up", "s08", "prescriptive", [conflict] * 9, outcome="abandoned")
    _session(ledger, "fallback", "s08", None, [conflict] * 9, enrolled=False)
    for arm in ("control", "prescriptive"):
        _session(ledger, f"field-{arm}", "s05", arm, [conflict], "incorrect", "claude", "field")
    _session(ledger, "other-model", "s05", "prescriptive", [conflict], agent_label="claude")
    return path


@pytest.fixture
def ledger(db: Path) -> Ledger:
    """The seeded ledger, opened read-only as the demo scripts open it."""
    return Ledger(make_engine(_settings(db), read_only=True), create_schema=False)


def _words(text: str) -> str:
    """Only the words, so line wrapping and box-drawing characters do not matter."""
    return " ".join(re.findall(r"[\w'.:-]+", text))


def _render(ledger: Ledger, pair: pairs.Pair) -> tuple[str, int]:
    console = Console(record=True, width=160, file=io.StringIO())
    pauses = []
    transcripts = ledger.tool_calls_for([pair.control.session_id, pair.challenger.session_id])
    render_replay(console, pair, transcripts, lambda: pauses.append(1))
    return console.export_text(), len(pauses)


def test_replay_renders_both_sessions_side_by_side_and_ends_with_outcomes(
    ledger: Ledger,
) -> None:
    pair = choose_pair(ledger, scenario_id="s05", model=MODEL)
    text, pauses = _render(ledger, pair)

    assert f"{BANNER} (recorded" in text
    assert "lab traffic" in text
    assert "s05 (trap)" in text
    assert MODEL in text
    assert "ctrl-s05" in text
    assert "presc-s05" in text
    assert _words(get_scenario("s05").brief[:60]) in _words(text)
    assert text.count("first styled error") == 2
    assert "suggestion" in text
    assert "corrected_call: book_room" in text
    assert "ERROR ROOM_CONFLICT" in text
    assert "booked B002: R3 2027-03-18 10:00-11:00" in text
    assert "outcome: correct" in text
    assert "tool calls: 3" in text
    assert "tool calls: 2" in text
    assert text.rstrip().endswith(BANNER)
    assert pauses == 3  # one pause per step; the longer transcript has three calls
    # The control column is on the left: its line starts before the challenger's.
    header = next(line for line in text.splitlines() if line.startswith("control: informative"))
    assert header.index("control") < header.index("prescriptive: error with suggestion")


def test_named_sessions_are_ordered_by_arm_and_checked(ledger: Ledger) -> None:
    pair = choose_pair(ledger, session_ids=["presc-s08", "ctrl-s08"])
    assert (pair.control.session_id, pair.challenger.session_id) == ("ctrl-s08", "presc-s08")
    text, _ = _render(ledger, pair)
    assert "outcome: no_booking" in text

    refusals = {
        "same scenario": ["ctrl-s05", "presc-s08"],
        "one control and one challenger": ["ctrl-s05", "ctrl-s08"],
        "unknown": ["ctrl-s05", "nope"],
        "same source": ["ctrl-s05", "field-prescriptive"],
        "same model": ["ctrl-s05", "other-model"],
        "smoke": ["ctrl-s05", "smoke"],
    }
    for reason, session_ids in refusals.items():
        with pytest.raises(ReplayError, match=reason):
            choose_pair(ledger, session_ids=session_ids)


def test_scenario_without_both_arms_is_refused(ledger: Ledger) -> None:
    with pytest.raises(ReplayError, match="no scored lab pair"):
        choose_pair(ledger, scenario_id="s01")
    with pytest.raises(ReplayError, match="model other"):
        choose_pair(ledger, scenario_id="s05", model="other")
    with pytest.raises(ReplayError, match="no scored lab pair"):
        choose_pair(ledger, scenario_id="s05", since=datetime.now(UTC) + timedelta(days=1))
    field = choose_pair(ledger, scenario_id="s05", source="field")
    assert field.control.session_id == "field-control"


def test_population_is_what_sa_report_scores(ledger: Ledger) -> None:
    exposed = ledger.exposed_sessions()
    lab = {r.session_id for r in population(exposed, "lab")}
    assert lab == {"ctrl-s05", "presc-s05", "ctrl-s08", "presc-s08", "lonely", "other-model"}
    assert {r.session_id for r in population(exposed, "field")} == {
        "field-control",
        "field-prescriptive",
    }
    assert ledger.exposed_sessions(since=datetime.now(UTC) + timedelta(days=1)) == []
    assert len(ledger.exposed_sessions(since=EPOCH)) == len(exposed)


def test_pairs_count_unfavourable_patterns_per_variant(ledger: Ledger) -> None:
    found = all_pairs(population(ledger.exposed_sessions(), "lab"))
    assert {(p.control.session_id, p.challenger.session_id) for p in found} == {
        ("ctrl-s05", "presc-s05"),
        ("ctrl-s08", "presc-s08"),
    }
    assert pattern_counts(found) == {
        ("trap", "prescriptive", OutcomePattern.SAME, CallsPattern.FEWER): 1,
        ("aligned", "prescriptive", OutcomePattern.ONLY_CONTROL_CORRECT, CallsPattern.FEWER): 1,
    }


def test_find_pairs_report_is_per_source_and_leaves_out_unscored(ledger: Ledger) -> None:
    console = Console(record=True, width=220, file=io.StringIO())
    pairs.report(console, ledger.exposed_sessions(), "lab", EPOCH)
    text = console.export_text()

    lab = population(ledger.exposed_sessions(), "lab")
    assert session_outcomes(lab)[("aligned", "prescriptive", "no_booking")] == 1
    assert "lab sessions since 2000-01-01 00:00 UTC" in text
    assert "field" not in text
    assert "only control correct" in text
    assert "OUTCOME DIFFERS" in text
    assert "only control sessions; no pair" in text
    assert SMOKE_AGENT_LABEL not in text
    assert "pilot" not in text
    assert "gave-up" not in text
    assert "fallback" not in text
    # The fallback has no arm, so it is in no row; the abandoned session is counted as such.
    rows = [_words(line).split() for line in text.splitlines()]
    assert ["aligned", "control", "2", "correct", "2", "2.00", "0"] in rows
    assert ["aligned", "prescriptive", "1", "no_booking", "1", "1.00", "1"] in rows


def test_read_only_engine_refuses_writes(db: Path) -> None:
    engine = make_engine(_settings(db), read_only=True)
    with pytest.raises(OperationalError, match="readonly"), engine.begin() as conn:
        conn.execute(
            insert(sessions).values(
                session_id="x",
                scenario_id="s01",
                scenario_kind="aligned",
                source="lab",
                agent_label="x",
                started_at=datetime.now(UTC),
            )
        )


def _state(directory: Path, started_at: datetime) -> None:
    """Write the run state `sa start` would leave for error-style-v1."""
    state = directory / "experiments" / ".state"
    state.mkdir(parents=True)
    (state / "error-style-v1.json").write_text(
        json.dumps(
            {
                "experiment_id": 1,
                "environment_key": "env",
                "metric_ids": {},
                "started_at": started_at.isoformat(),
                "commit": "abc",
                "feature_id": 1,
                "variant_ids": {},
            }
        ),
        encoding="utf-8",
    )


def test_scripts_run_end_to_end(
    db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pairs, "get_settings", lambda: _settings(db))
    monkeypatch.chdir(tmp_path)
    _state(tmp_path, EPOCH)

    assert replay.main(["--scenario", "s05", "--delay", "0", "--width", "160"]) == 0
    assert BANNER in capsys.readouterr().out
    assert replay.main(["--scenario", "s01", "--width", "160"]) == 2
    assert "no scored lab pair" in capsys.readouterr().out
    assert pairs.main(["--width", "200"]) == 0
    assert "lab sessions since 2000-01-01" in capsys.readouterr().out
    assert pairs.main(["--width", "200", "--source", "field"]) == 0
    assert "field-control" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        replay.main(["--sessions", "ctrl-s05", "presc-s05", "--model", MODEL])
    assert "only with --scenario: --model" in capsys.readouterr().err


def test_scripts_report_unreadable_inputs_briefly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert pairs.main(["--slug", "missing"]) == 2
    assert "has `sa start` run" in capsys.readouterr().out

    _state(tmp_path, EPOCH)
    empty = tmp_path / "empty.db"
    monkeypatch.setattr(pairs, "get_settings", lambda: _settings(empty))
    assert pairs.main([]) == 2
    assert "cannot read the ledger" in capsys.readouterr().out


def test_replay_stops_cleanly_when_input_ends(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pairs, "get_settings", lambda: _settings(db))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert replay.main(["--sessions", "ctrl-s05", "presc-s05", "--step"]) == 2
    assert "input ended" in capsys.readouterr().out
