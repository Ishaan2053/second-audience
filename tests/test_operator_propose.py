"""`sa propose`: evidence from the ledger, validation of the model's answer, and the CLI."""

import json
import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from test_operator_start import NOW, SERVER_KEY, FakeAdmin, _git
from typer.testing import CliRunner

from second_audience.config import Settings
from second_audience.core.ledger import Ledger, ToolCallRecord, audit_log
from second_audience.core.styles import ErrorStyle
from second_audience.operator import cli
from second_audience.operator.analysis import SMOKE_AGENT_LABEL
from second_audience.operator.doctor import run_checks
from second_audience.operator.propose import (
    DRAFT_HEADER,
    SETTING_MEANINGS,
    Evidence,
    ProposalRejectedError,
    build_evidence,
    followed_suggestions,
    rationale_markdown,
    rendered_behaviour,
    validate,
)
from second_audience.operator.repo import Repo
from second_audience.operator.spec import load_spec

SPEC_SOURCE = Path(__file__).parents[1] / "experiments" / "error-style-v1.yaml"
SLUG = "error-style-v1"
NEW_SLUG = "error-style-v2"
STARTED = "2026-11-26T15:00:00Z"
AT = datetime(2026, 11, 26, 16, 0, tzinfo=UTC)


def sid(n: int) -> str:
    """A session ID shaped like the server's (`uuid4().hex`)."""
    return uuid.UUID(int=n).hex


BOOK = {"room_id": "R3", "date": "2027-03-18", "start": "11:00", "end": "12:00", "attendees": 4}
FIX = {**BOOK, "start": "12:00", "end": "13:00"}
CONFLICT = {"error": {"code": "ROOM_CONFLICT", "message": "Room R3 is already booked."}}
OFFER = {
    **CONFLICT,
    "corrected_call": {"tool": "book_room", "arguments": {"session_id": "x", **FIX}},
}
OK = {"booking_id": "B1"}

# (id, arm, kind, outcome, calls); each call is (tool, arguments, result, error_code).
Call = tuple[str, dict[str, Any], Any, str | None]
SESSIONS: list[tuple[str, str | None, str, str, list[Call]]] = [
    (sid(1), "control", "aligned", "correct", [("book_room", BOOK, OK, None)] * 6),
    (
        sid(2),
        "control",
        "trap",
        "no_booking",
        [("book_room", BOOK, CONFLICT, "ROOM_CONFLICT")] * 6,
    ),
    (
        sid(3),
        "prescriptive",
        "trap",
        "incorrect",
        [("book_room", BOOK, OFFER, "ROOM_CONFLICT"), ("book_room", FIX, OK, None)],
    ),
    (
        sid(4),
        "prescriptive",
        "aligned",
        "budget_exhausted",
        [("book_room", BOOK, OFFER, "ROOM_CONFLICT"), ("list_rooms", {}, [], None)],
    ),
    (sid(5), "prescriptive", "aligned", "correct", [("book_room", FIX, OK, None)] * 2),
    (sid(6), None, "aligned", "no_booking", [("list_rooms", {}, [], None)]),  # never exposed
]


def seed(ledger: Ledger) -> None:
    """The sessions above, each exposed (except the last) and finished."""
    for session_id, arm, kind, outcome, calls in SESSIONS:
        ledger.start_session(session_id, "s05", kind, "lab", "gemini-a", None)
        if arm is not None:
            ledger.record_exposure(session_id, arm, in_experiment=True)
        for seq, (tool, arguments, result, code) in enumerate(calls, 1):
            sent = {"session_id": session_id, **arguments}
            ledger.record_tool_call(session_id, seq, tool, sent, result, code, code is not None)
        ledger.finish_session(session_id, outcome, len(calls), final_state=outcome)


def answer(**overrides: Any) -> str:
    """A valid model answer, with fields replaced as given."""
    body: dict[str, Any] = {
        "slug": NEW_SLUG,
        "variant_key": "cautious",
        "variant_settings": ["suggest_alternative", "corrected_call", "caution_note"],
        "primary_metric": "Wrong booking",
        "observed_pattern": f"On the trap in {sid(3)} the agent sent the corrected call as is.",
        "hypothesis": "A caution note lowers wrong bookings on traps without more tool calls.",
        "rationale": "Agents copy the corrected call without checking the request.",
        "cited_sessions": [sid(3)],
    }
    return json.dumps({**body, **overrides})


class FakeModel:
    """Returns queued answers and records every prompt."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


class NoFlagsmith:
    """Any use of the admin wrapper fails the test."""

    def __getattr__(self, name: str) -> Any:
        pytest.fail(f"sa propose touched Flagsmith: {name}")


def call(seq: int, tool: str, arguments: dict[str, Any], result: Any) -> ToolCallRecord:
    return ToolCallRecord(seq, tool, arguments, result, False, None, False, AT)


# --- followed suggestion --------------------------------------------------------------------


def test_followed_suggestion_compares_the_next_call_with_the_corrected_call() -> None:
    calls = [
        call(1, "book_room", BOOK, OFFER),
        call(2, "book_room", {"session_id": "other", **FIX}, OK),  # followed; session ignored
        call(3, "book_room", BOOK, OFFER),
        call(4, "book_room", {**FIX, "room_id": "R1"}, OK),  # different room
        call(5, "book_room", BOOK, OFFER),
        call(6, "reschedule_booking", FIX, OK),  # different tool
        call(7, "book_room", BOOK, CONFLICT),  # no corrected call offered
        call(8, "book_room", BOOK, OFFER),  # last call: nothing followed
    ]
    assert followed_suggestions(calls) == {1: True, 3: False, 5: False, 8: False}


def test_setting_meanings_cover_every_error_style_setting() -> None:
    assert set(SETTING_MEANINGS) == set(ErrorStyle.model_fields)


# --- evidence and validation ----------------------------------------------------------------


@pytest.fixture
def evidence(tmp_path: Path) -> Evidence:
    ledger = Ledger(create_engine(f"sqlite:///{tmp_path / 'ledger.db'}"))
    seed(ledger)
    records = ledger.sessions_since(datetime(2020, 1, 1, tzinfo=UTC))
    transcripts = ledger.tool_calls_for(r.session_id for r in records)
    return build_evidence(load_spec(SPEC_SOURCE), records, transcripts, 10, [SLUG])


def test_evidence_counts_outcomes_and_suggestion_uptake(evidence: Evidence) -> None:
    rows = {(r["arm"], r["scenario_kind"]): r for r in evidence.outcome_rows}
    assert set(rows) == {
        ("control", "aligned"),
        ("control", "trap"),
        ("prescriptive", "aligned"),
        ("prescriptive", "trap"),
    }  # the unexposed session is left out
    assert rows[("prescriptive", "aligned")]["budget_exhausted"] == 1
    assert rows[("prescriptive", "aligned")]["mean_tool_calls"] == "2.00"
    uptake = {(r["arm"], r["scenario_kind"]): r for r in evidence.suggestion_rows}
    assert uptake[("prescriptive", "trap")] == {
        "arm": "prescriptive",
        "scenario_kind": "trap",
        "corrected_calls_offered": 1,
        "followed": 1,
    }
    assert uptake[("prescriptive", "aligned")]["followed"] == 0
    assert evidence.session_ids == {sid(2), sid(3), sid(4)}
    # Prescriptive sessions used fewer tool calls, so it is the best-known arm.
    assert evidence.control.key == "prescriptive"
    (step, _) = next(s for s in evidence.sessions if s["session_id"] == sid(3))["transcript"]
    assert "session_id" not in step["arguments"]
    assert step["followed_corrected_call"] is True


def test_ship_smoke_sessions_are_not_evidence(tmp_path: Path) -> None:
    ledger = Ledger(create_engine(f"sqlite:///{tmp_path / 'smoke.db'}"))
    seed(ledger)
    smoke = sid(99)
    ledger.start_session(smoke, "s01", "aligned", "lab", SMOKE_AGENT_LABEL, None)
    ledger.record_exposure(smoke, "control", in_experiment=True)
    ledger.finish_session(smoke, "no_booking", 3, final_state="no_booking")
    records = ledger.sessions_since(datetime(2020, 1, 1, tzinfo=UTC))
    assert smoke in {r.session_id for r in records}
    found = build_evidence(load_spec(SPEC_SOURCE), records, {}, 10, [])
    assert smoke not in found.session_ids
    control = next(
        r for r in found.outcome_rows if (r["arm"], r["scenario_kind"]) == ("control", "aligned")
    )
    assert control["sessions"] == 1  # the seeded session only


def test_sample_is_limited_and_spread_over_arms(evidence: Evidence, tmp_path: Path) -> None:
    ledger = Ledger(create_engine(f"sqlite:///{tmp_path / 'ledger.db'}"))
    records = ledger.sessions_since(datetime(2020, 1, 1, tzinfo=UTC))
    small = build_evidence(evidence.spec, records, {}, 2, [])
    assert len(small.sessions) == 2
    assert {s["arm"] for s in small.sessions} == {"control", "prescriptive"}


def test_valid_answer_builds_a_spec_against_the_best_known_arm(evidence: Evidence) -> None:
    draft = validate(answer(), evidence)
    spec, source = draft.spec, evidence.spec
    assert spec.slug == NEW_SLUG
    assert spec.environment == "production"
    assert spec.control.value == source.variants[0].value  # prescriptive is the control now
    assert spec.variants[0].style == ErrorStyle(
        suggest_alternative=True, corrected_call=True, caution_note=True
    )
    assert spec.allocation.split == {"control": 50, "cautious": 50}
    assert spec.stopping_rule == source.stopping_rule
    roles = {m.name: m.role for m in spec.metrics}
    assert roles == {
        "Wrong booking": "primary",
        "Tool calls per session": "secondary",
        "Task completed correctly": "guardrail",
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"variant_settings": ["suggest_alternative", "verbose_hint"]}, "unknown error style"),
        ({"variant_settings": ["corrected_call"]}, "only together with suggest_alternative"),
        # A note without a suggestion never renders, so this would be an A/A test.
        ({"variant_settings": ["caution_note"]}, "only together with suggest_alternative"),
        (
            {"variant_settings": ["suggest_alternative", "corrected_call"]},
            "already tested",
        ),
        ({"variant_settings": []}, "already tested"),
        ({"slug": SLUG}, "already taken"),
        ({"variant_key": "control"}, "must not be 'control'"),
        ({"variant_key": "prescriptive"}, "or an arm key of error-style-v1"),
        ({"primary_metric": "Happiness"}, "primary_metric must be one of"),
        ({"cited_sessions": [sid(99)]}, "cited sessions not in the evidence"),
        ({"cited_sessions": []}, "cite at least one session"),
        ({"rationale": f"Compare with {sid(1)}."}, "cited sessions not in the evidence"),
        ({"rationale": "Wrong bookings rose by 37 percent."}, "numbers not in the evidence"),
        ({"slug": "Error Style"}, "spec built from it is invalid"),
    ],
)
def test_invalid_answers_are_rejected(
    evidence: Evidence, overrides: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ProposalRejectedError, match=reason):
        validate(answer(**overrides), evidence)


def test_styles_compare_by_what_they_render() -> None:
    control = rendered_behaviour(ErrorStyle())
    assert rendered_behaviour(ErrorStyle(caution_note=True)) == control
    assert rendered_behaviour(ErrorStyle(suggest_alternative=True, caution_note=True)) != control


def test_invented_count_is_rejected_even_inside_a_session_id(evidence: Evidence) -> None:
    hidden = "42" + sid(3)[2:]  # the digits 42 occur only inside this session ID
    session = {**evidence.sessions[0], "session_id": hidden}
    tweaked = replace(evidence, sessions=[session])
    assert "42" in json.dumps(tweaked.pack())
    cited = {"cited_sessions": [hidden], "observed_pattern": f"See {hidden}."}
    with pytest.raises(ProposalRejectedError, match=r"numbers not in the evidence: \['42'\]"):
        validate(answer(**cited, rationale=f"In {hidden}, 42 agents gave up."), tweaked)
    # The ID itself, the slug and computed figures pass.
    fine = f"In {hidden} of error-style-v1 the trap sessions averaged 2.00 tool calls."
    assert validate(answer(**cited, rationale=fine), tweaked).spec.slug == NEW_SLUG


def test_model_labels_and_the_proposals_own_names_are_not_numbers(evidence: Evidence) -> None:
    model = "gemini-3.5-flash-lite"
    tweaked = replace(evidence, sessions=[{**s, "model": model} for s in evidence.sessions])
    names = {"slug": "error-style-v7", "variant_key": "note_v3"}
    prose = f"In {sid(3)}, {model} copied the call; error-style-v7 adds note_v3 to it."
    assert "3.5" not in json.dumps(tweaked.figures())  # so the label alone would fail
    assert validate(answer(**names, rationale=prose), tweaked).spec.slug == "error-style-v7"
    with pytest.raises(ProposalRejectedError, match=r"numbers not in the evidence: \['17'\]"):
        validate(answer(**names, rationale=f"{prose} {model} gave up 17 times."), tweaked)


def test_a_digit_only_model_label_does_not_erase_real_numbers(evidence: Evidence) -> None:
    tweaked = replace(evidence, sessions=[{**s, "model": "40"} for s in evidence.sessions])
    assert "40" not in json.dumps(tweaked.figures())
    with pytest.raises(ProposalRejectedError, match=r"numbers not in the evidence: \['40'\]"):
        validate(answer(rationale="Wrong bookings rose 40% in the trap sessions."), tweaked)


def test_a_digit_only_label_is_not_stripped_from_inside_a_number(evidence: Evidence) -> None:
    tweaked = replace(evidence, sessions=[{**s, "model": "3"} for s in evidence.sessions])
    assert "35" not in json.dumps(tweaked.figures())
    with pytest.raises(ProposalRejectedError, match=r"numbers not in the evidence: \['35'\]"):
        validate(answer(rationale="Agents gave up 35 times."), tweaked)


def test_a_name_is_stripped_only_as_a_whole_token(evidence: Evidence) -> None:
    names = {"slug": "error-style-v7", "variant_key": "note_v3"}
    fine = "error-style-v7 adds note_v3. The note_v3 arm is new."
    assert validate(answer(**names, rationale=fine), evidence).spec.slug == "error-style-v7"
    with pytest.raises(ProposalRejectedError, match="numbers not in the evidence"):
        validate(answer(**names, rationale="It is like error-style-v71 before it."), evidence)


def test_transcript_arguments_are_not_a_source_of_numbers(evidence: Evidence) -> None:
    # 18 occurs only in the transcripts' arguments (date 2027-03-18), never in a figure.
    assert "18" in json.dumps(evidence.pack())
    with pytest.raises(ProposalRejectedError, match="numbers not in the evidence"):
        validate(answer(rationale="Agents failed 18 times."), evidence)


def test_rationale_table_cells_escape_pipes_and_line_breaks(evidence: Evidence) -> None:
    sessions = [{**s, "model": "evil | agent\n| injected |"} for s in evidence.sessions]
    tweaked = replace(evidence, sessions=sessions)
    text = rationale_markdown(tweaked, validate(answer(), tweaked), "x.yaml", "m", AT)
    row = next(line for line in text.splitlines() if line.startswith(f"| `{sid(3)}`"))
    assert r"| evil \| agent \| injected \| |" in row
    assert not any(line.startswith("| injected") for line in text.splitlines())


def test_malformed_answer_is_rejected(evidence: Evidence) -> None:
    with pytest.raises(ProposalRejectedError, match="does not match the schema"):
        validate('{"slug": "x"}', evidence)


# --- the command ----------------------------------------------------------------------------


class Workspace:
    """A working directory with the source spec, its state file and a seeded ledger."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, model: FakeModel | None):
        (root / "experiments" / ".state").mkdir(parents=True, exist_ok=True)
        if not (root / "experiments" / SPEC_SOURCE.name).exists():
            shutil.copy(SPEC_SOURCE, root / "experiments" / SPEC_SOURCE.name)
        state = {
            "experiment_id": 680,
            "environment_key": "envkey",
            "metric_ids": {},
            "feature_id": 1,
            "variant_ids": {"prescriptive": 2},
        }
        (root / "experiments" / ".state" / f"{SLUG}.json").write_text(
            json.dumps({**state, "started_at": STARTED, "commit": "abc123"}), encoding="utf-8"
        )
        monkeypatch.chdir(root)
        self.root = root
        self.database_url = f"sqlite:///{root.parent / 'ledger.db'}"
        self.ledger = Ledger(create_engine(self.database_url))
        seed(self.ledger)
        self.admin_requests = 0
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            sa_database_url=SecretStr(self.database_url),
            sa_env="test",
            flagsmith_environment_key=SecretStr(SERVER_KEY),
        )

        def make_admin(s: Settings) -> Any:
            self.admin_requests += 1
            return NoFlagsmith()

        self.context = cli.Context(
            load_settings=lambda: settings,
            make_admin=make_admin,
            checks=lambda *args: pytest.fail("sa propose runs no doctor checks"),
            open_repo=lambda path: pytest.fail("sa propose needs no git"),
            open_ledger=lambda s: self.ledger,
            now=lambda: NOW,
            make_drafter=lambda s: None,
            sleep=lambda seconds: None,
            make_proposer=lambda s: model,
            smoke=lambda s, label: pytest.fail("sa propose runs no smoke session"),
        )
        monkeypatch.setattr(cli, "make_context", lambda: self.context)

    @property
    def proposals(self) -> Path:
        return self.root / "experiments" / "proposals"

    def audit_rows(self) -> list[Any]:
        with create_engine(self.database_url).connect() as conn:
            return list(conn.execute(select(audit_log)))


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli.app, list(args))


def test_propose_writes_a_valid_spec_and_rationale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel(answer())
    workspace = Workspace(tmp_path / "repo", monkeypatch, model)
    result = invoke("propose", "--limit", "5")
    assert result.exit_code == 0, result.output
    spec_file = workspace.proposals / f"{NEW_SLUG}.yaml"
    assert f"Spec: experiments/proposals/{NEW_SLUG}.yaml" in result.output
    assert f"move the spec to experiments/{NEW_SLUG}.yaml" in result.output
    assert f"add a {NEW_SLUG} entry to HYPOTHESIS.md" in result.output
    assert f"then `sa start experiments/{NEW_SLUG}.yaml`" in result.output

    text = spec_file.read_text(encoding="utf-8")
    assert text.startswith(f"# {DRAFT_HEADER}")
    spec = load_spec(spec_file)
    assert spec.variants[0].key == "cautious"
    assert yaml.safe_load(text)["stopping_rule"]["hard_end"] == "2026-11-29T12:00:00+05:30"

    rationale = (workspace.proposals / f"{NEW_SLUG}.md").read_text(encoding="utf-8")
    assert f"> **{DRAFT_HEADER}**" in rationale
    assert "| prescriptive | trap | 1 | 0 | 1 | 0 | 0 | 0 | 2.00 |" in rationale
    assert "| prescriptive | trap | 1 | 1 |" in rationale
    assert f"| `{sid(3)}` | prescriptive | s05 | trap | gemini-a | incorrect | 2 | yes |" in (
        rationale
    )
    # Every cited session exists in the ledger.
    cited = json.loads(answer())["cited_sessions"]
    assert len(workspace.ledger.session_summaries(cited)) == len(cited)

    (row,) = workspace.audit_rows()
    assert (row.action, row.experiment_slug, row.approved_by) == ("propose", NEW_SLUG, None)
    assert json.loads(row.detail)["cited_sessions"] == cited
    assert workspace.admin_requests == 0
    (prompt,) = model.prompts
    assert json.loads(prompt)["source_experiment"] == SLUG


def test_unknown_setting_is_rejected_and_retried_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel(answer(variant_settings=["suggest_alternative", "verbose_hint"]), answer())
    workspace = Workspace(tmp_path / "repo", monkeypatch, model)
    result = invoke("propose")
    assert result.exit_code == 0, result.output
    assert "First answer rejected (unknown error style settings ['verbose_hint']" in result.output
    assert len(model.prompts) == 2
    assert "Your previous answer was rejected: unknown error style settings" in model.prompts[1]
    assert (workspace.proposals / f"{NEW_SLUG}.yaml").exists()
    assert json.loads(workspace.audit_rows()[0].detail)["attempts"] == 2


def test_two_rejections_exit_non_zero_and_save_the_raw_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = [answer(cited_sessions=[sid(99)]), answer(cited_sessions=[sid(98)])]
    workspace = Workspace(tmp_path / "repo", monkeypatch, FakeModel(*bad))
    result = invoke("propose")
    assert result.exit_code == 1
    assert "rejected twice" in result.output
    (saved,) = (workspace.proposals / ".failed").glob("*.json")
    assert [a["raw"] for a in json.loads(saved.read_text(encoding="utf-8"))] == bad
    assert list(workspace.proposals.glob("*.yaml")) == []
    assert workspace.audit_rows() == []


def test_without_a_model_key_the_evidence_is_printed_and_nothing_is_drafted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path / "repo", monkeypatch, None)
    result = invoke("propose")
    assert result.exit_code == 1
    assert '"outcomes_by_arm_and_kind"' in result.output
    assert sid(3) in result.output
    assert "GEMINI_API_KEY is not set, so no proposal was drafted" in result.output
    assert not workspace.proposals.exists()
    assert workspace.audit_rows() == []


def test_model_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Down(FakeModel):
        def __call__(self, prompt: str) -> str:
            raise ConnectionError("unreachable")

    workspace = Workspace(tmp_path / "repo", monkeypatch, Down())
    result = invoke("propose")
    assert result.exit_code == 1
    assert "the model call failed: ConnectionError" in result.output
    assert not workspace.proposals.exists()


def test_start_dry_run_accepts_the_generated_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "experiments").mkdir(parents=True)
    shutil.copy(SPEC_SOURCE, root / "experiments" / SPEC_SOURCE.name)
    workspace = Workspace(root, monkeypatch, FakeModel(answer()))
    assert invoke("propose").exit_code == 0

    # The human reviews, moves the spec, pre-registers the slug and commits, as the next
    # step says.
    (root / "experiments" / "proposals" / f"{NEW_SLUG}.yaml").rename(
        root / "experiments" / f"{NEW_SLUG}.yaml"
    )
    (root / "HYPOTHESIS.md").write_text(f"# Hypothesis\n\nExperiment {NEW_SLUG}.\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test Approver")
    _git(root, "config", "user.email", "approver@example.com")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "proposal")

    admin = FakeAdmin()

    def checks(load: Any, spec_path: Path, make_admin: Any) -> Any:
        return run_checks(
            load, spec_path, make_admin, database=lambda s: "sqlite", server=lambda url: None
        )

    settings = workspace.context.load_settings().model_copy(
        update={"gemini_api_key": SecretStr("gemini"), "sa_server_url": "http://127.0.0.1:1/mcp"}
    )
    context = replace(
        workspace.context,
        load_settings=lambda: settings,
        make_admin=lambda s: admin,
        checks=checks,
        open_repo=Repo.discover,
    )
    monkeypatch.setattr(cli, "make_context", lambda: context)
    result = invoke("start", f"experiments/{NEW_SLUG}.yaml", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "REFUSED" not in result.output
    assert "create variant cautious" in result.output
    assert f"START experiment {NEW_SLUG}" in result.output
    assert admin.changes == []
