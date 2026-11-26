"""`sa status` and `sa report`: results parsing, analysis, narrative check and the CLI."""

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from typer.testing import CliRunner

from second_audience.config import Settings
from second_audience.core.ledger import Ledger, SessionRecord, audit_log
from second_audience.operator import cli
from second_audience.operator.analysis import (
    SMOKE_AGENT_LABEL,
    Decision,
    RunState,
    analyse,
    breakdown,
    reconcile,
)
from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError
from second_audience.operator.narrative import (
    NARRATIVE_INSTRUCTIONS,
    facts,
    template,
    unsupported_numbers,
    write_narrative,
)
from second_audience.operator.report import render
from second_audience.operator.results import (
    ExperimentResults,
    Inference,
    load_manual,
    refresh_and_save,
)
from second_audience.operator.spec import ExperimentSpec, load_spec

SPEC_SOURCE = Path(__file__).parents[1] / "experiments" / "error-style-v1.yaml"
SLUG = "error-style-v1"
ENV = "envkey"
PRIMARY, COMPLETED, WRONG = 665, 666, 667
STARTED = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)  # before the hard end (2026-11-29 06:30 UTC)
AFTER_END = datetime(2026, 11, 30, tzinfo=UTC)
FAR_FUTURE = "2099-01-01T00:00:00Z"

STATE = {
    "slug": SLUG,
    "environment": "production",
    "environment_key": ENV,
    "flag": "error_style",
    "feature_id": 1,
    "variant_ids": {"prescriptive": 2},
    "metric_ids": {
        "Tool calls per session": PRIMARY,
        "Task completed correctly": COMPLETED,
        "Wrong booking": WRONG,
    },
    "experiment_id": 680,
    "started_at": "2026-11-26T15:00:00Z",
    "commit": "abc123",
    "approved_by": "dev",
    "recorded_at": "2026-11-26T15:00:01+00:00",
}


def _inference(lift: float, low: float, high: float, chance: float) -> dict[str, float]:
    return {"lift": lift, "ci_low": low, "ci_high": high, "chance_to_win": chance}


def results_json(
    as_of: str | None = FAR_FUTURE,
    n: tuple[int, int] = (3, 3),
    sums: dict[int, tuple[float, float]] | None = None,
    inference: dict[int, dict[str, float] | None] | None = None,
) -> dict[str, Any]:
    """A results payload in the shape recorded in docs/spike-findings.md (question 5)."""
    if as_of is None:
        return {"results": None}
    sums = sums or {PRIMARY: (15.0, 9.0), COMPLETED: (3.0, 1.0), WRONG: (0.0, 1.0)}
    inference = inference or {}
    metrics = [
        {
            "metric_id": metric_id,
            "variants": {
                "control": {"n": n[0], "sum": control, "sum_squares": 0.0},
                "prescriptive": {"n": n[1], "sum": variant, "sum_squares": 0.0},
            },
            "inference": {"prescriptive": inference.get(metric_id)},
        }
        for metric_id, (control, variant) in sums.items()
    ]
    return {
        "results": {
            "as_of": as_of,
            "is_final": False,
            "payload": {"srm_p_value": None, "metrics": metrics},
        }
    }


def record(
    variant: str | None,
    *,
    kind: str = "aligned",
    final_state: str = "correct",
    outcome: str | None = None,
    calls: int = 4,
    source: str = "lab",
    model: str = "gemini-a",
    in_experiment: bool | None = True,
    at: datetime = STARTED + timedelta(hours=1),
) -> SessionRecord:
    """A ledger row; `outcome` defaults to the final state."""
    exposed = variant is not None or in_experiment is False
    return SessionRecord(
        session_id=f"s{id(object())}",
        scenario_id="s05" if kind == "trap" else "s01",
        scenario_kind=kind,
        source=source,
        agent_label=model,
        variant=variant,
        in_experiment=in_experiment if exposed else None,
        started_at=at,
        exposed_at=at if exposed else None,
        finished_at=at,
        outcome=outcome or final_state,
        final_state=final_state,
        tool_call_count=calls,
    )


# Three enrolled sessions per arm, matching `results_json()`'s default sums.
RECORDS = [
    record("control", calls=5),
    record("control", calls=5, kind="trap"),
    record("control", calls=5, model="gemini-b"),
    record("prescriptive", calls=3),
    record("prescriptive", calls=3, kind="trap", final_state="incorrect"),
    record("prescriptive", calls=3, final_state="no_booking"),
    record(None, in_experiment=None),  # never exposed
    record(None, in_experiment=False),  # exposed while Flagsmith was down
    record("control", outcome="abandoned", calls=2),  # abandoned: no events
]


@pytest.fixture
def spec() -> ExperimentSpec:
    return load_spec(SPEC_SOURCE)


@pytest.fixture
def state(tmp_path: Path) -> RunState:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(STATE), encoding="utf-8")
    return RunState.load(path)


def run(
    spec: ExperimentSpec,
    state: RunState,
    raw: dict[str, Any],
    records: list[SessionRecord] = RECORDS,
    now: datetime = NOW,
    manual: dict[str, Inference] | None = None,
) -> Any:
    return analyse(spec, state, ExperimentResults.from_api(raw), manual or {}, records, now)


# --- results parsing ------------------------------------------------------------------------


def test_results_parse_and_null_before_first_refresh() -> None:
    empty = ExperimentResults.from_api({"results": None})
    assert empty.metrics == []
    assert empty.as_of is None
    parsed = ExperimentResults.from_api(
        results_json(inference={PRIMARY: _inference(-0.4, -0.6, -0.1, 0.99)})
    )
    primary = parsed.metric(PRIMARY)
    assert primary is not None
    assert primary.variants["control"].mean == 5.0
    assert primary.inference["prescriptive"] is not None
    assert primary.inference["prescriptive"].chance_to_win == 0.99
    assert parsed.metric(999) is None


def test_manual_values_fill_missing_inference_and_are_marked(
    tmp_path: Path, spec: ExperimentSpec, state: RunState
) -> None:
    (tmp_path / "manual.yaml").write_text(
        "metrics:\n  Tool calls per session:\n"
        "    {lift: -0.3, ci_low: -0.5, ci_high: -0.1, chance_to_win: 0.97}\n",
        encoding="utf-8",
    )
    manual = load_manual(tmp_path)
    analysis = run(spec, state, results_json(n=(80, 80)), manual=manual)
    assert analysis.rows[0].inference_source == "from dashboard"
    assert analysis.decision.decision is Decision.SHIP_VARIANT
    text = render(analysis, write_narrative(analysis, None, "m"), "r.json", "refreshed")
    assert "prescriptive against control (from dashboard): lift -0.300" in text


def test_manual_file_must_have_metrics(tmp_path: Path) -> None:
    assert load_manual(tmp_path) == {}
    (tmp_path / "manual.yaml").write_text("lift: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="metrics"):
        load_manual(tmp_path)


# --- decision rule --------------------------------------------------------------------------


def test_no_decision_before_the_stopping_rule(spec: ExperimentSpec, state: RunState) -> None:
    raw = results_json(n=(74, 90), inference={PRIMARY: _inference(-0.4, -0.6, -0.2, 0.999)})
    analysis = run(spec, state, raw)
    assert analysis.decision.decision is Decision.INCONCLUSIVE
    assert not analysis.decision.rule_met
    text = render(analysis, write_narrative(analysis, None, "m"), "r.json", "refreshed")
    assert "0.999" not in text
    assert "withheld until the stopping rule is met" in text


@pytest.mark.parametrize(
    ("inference", "expected"),
    [
        ({PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97)}, Decision.SHIP_VARIANT),  # clear win
        ({PRIMARY: _inference(0.3, 0.1, 0.5, 0.02)}, Decision.KEEP_CONTROL),  # clear loss
        ({PRIMARY: _inference(0.2, 0.0, 0.4, 0.05)}, Decision.KEEP_CONTROL),  # control at 0.95
        ({PRIMARY: _inference(-0.1, -0.3, 0.1, 0.80)}, Decision.INCONCLUSIVE),
        ({PRIMARY: _inference(0.1, -0.1, 0.3, 0.06)}, Decision.INCONCLUSIVE),  # control 0.94
        (
            {
                PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97),
                WRONG: _inference(0.8, 0.2, 1.4, 0.01),  # wrong bookings credibly up
            },
            Decision.KEEP_CONTROL,
        ),
        (
            {
                PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97),
                COMPLETED: _inference(-0.3, -0.5, -0.05, 0.02),  # completion credibly down
            },
            Decision.KEEP_CONTROL,
        ),
        (
            {
                PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97),
                COMPLETED: _inference(-0.1, -0.3, 0.1, 0.2),  # interval spans zero
            },
            Decision.SHIP_VARIANT,
        ),
        ({}, Decision.INCONCLUSIVE),
    ],
)
def test_decision_rule(
    spec: ExperimentSpec,
    state: RunState,
    inference: dict[int, dict[str, float]],
    expected: Decision,
) -> None:
    analysis = run(spec, state, results_json(n=(75, 75), inference=inference))
    assert analysis.decision.rule_met
    assert analysis.decision.decision is expected


def test_hard_end_triggers_the_read_and_lists_trap_wrong_bookings(
    spec: ExperimentSpec, state: RunState
) -> None:
    raw = results_json(inference={PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97)})
    analysis = run(spec, state, raw, now=AFTER_END)
    assert analysis.decision.rule_met
    assert [r.scenario_kind for r in analysis.trap_wrong] == ["trap"]
    text = render(analysis, write_narrative(analysis, None, "m"), "r.json", "refreshed")
    assert "Wrong bookings by prescriptive on trap scenarios (ledger):" in text


# --- reconciliation, breakdowns, split ------------------------------------------------------


def test_reconciliation_matches_the_ledger(spec: ExperimentSpec, state: RunState) -> None:
    # The abandoned control session was exposed, so Flagsmith counts 4 control identities.
    analysis = run(spec, state, results_json(n=(4, 3)))
    assert {r.cause for r in analysis.reconciliation} == {"match"}
    by_key = {(r.measure, r.arm): r for r in analysis.reconciliation}
    assert by_key[("Exposed sessions", "control")].ledger == 4  # abandoned still exposed
    assert by_key[("task_finished (sum)", "control")].ledger == 15  # abandoned sends nothing
    assert by_key[("task_wrong_booking (sum)", "prescriptive")].ledger == 1


def test_reconciliation_explains_latency_and_losses(spec: ExperimentSpec, state: RunState) -> None:
    rows = run(spec, state, results_json(n=(4, 3))).rows
    early = datetime.fromisoformat("2026-11-26T15:30:00+00:00")
    latency = reconcile(spec, rows, RECORDS, early)
    assert all(r.ledger_at_as_of == 0 for r in latency)
    lost = replace(RECORDS[0], session_id="extra", exposed_at=STARTED + timedelta(minutes=10))
    rows_lost = reconcile(spec, rows, [*RECORDS, lost], datetime.fromisoformat(FAR_FUTURE))
    exposed = next(r for r in rows_lost if (r.measure, r.arm) == ("Exposed sessions", "control"))
    assert exposed.difference == 1
    assert "not explained by timing" in exposed.cause


def test_breakdown_maths(spec: ExperimentSpec) -> None:
    cells = {(c.group, c.arm): c for c in breakdown(spec, RECORDS, lambda r: r.scenario_kind)}
    aligned = cells[("aligned", "prescriptive")]
    assert aligned.n == 2  # abandoned, unexposed and fallback sessions excluded
    assert aligned.correct is not None
    assert aligned.correct.successes == 1
    assert aligned.wrong is not None
    assert aligned.wrong.successes == 0
    assert aligned.mean_tool_calls == 3
    trap = cells[("trap", "prescriptive")]
    assert trap.wrong is not None
    assert trap.wrong.rate == 1.0
    assert cells[("aligned", "control")].n == 2


def test_traffic_counts_fallback_and_outcomes(spec: ExperimentSpec, state: RunState) -> None:
    analysis = run(spec, state, results_json())
    (lab,) = [t for t in analysis.traffic if t.model == "gemini-a"]
    assert lab.sessions == 8
    assert lab.exposed == 7
    assert lab.enrolled == 6
    assert lab.fallback == 1
    assert lab.outcomes["abandoned"] == 1


def test_ship_smoke_sessions_are_left_out(spec: ExperimentSpec, state: RunState) -> None:
    smoke = record("prescriptive", kind="trap", final_state="incorrect", model=SMOKE_AGENT_LABEL)
    analysis = run(spec, state, results_json(), records=[*RECORDS, smoke])
    assert smoke not in analysis.records
    assert all(t.model != SMOKE_AGENT_LABEL for t in analysis.traffic)
    assert all(c.group != SMOKE_AGENT_LABEL for c in analysis.breakdowns["By model"])
    assert len(analysis.trap_wrong) == 1  # only the seeded one, not the smoke session
    assert analysis == run(spec, state, results_json())


def test_split_health_uses_flagsmith_counts(spec: ExperimentSpec, state: RunState) -> None:
    analysis = run(spec, state, results_json(n=(70, 30)))
    assert analysis.srm is not None
    assert analysis.srm.mismatch
    text = render(analysis, write_narrative(analysis, None, "m"), "r.json", "refreshed")
    assert "sample-ratio mismatch (p < 0.01)" in text


def test_report_has_all_nine_sections(spec: ExperimentSpec, state: RunState) -> None:
    analysis = run(spec, state, results_json())
    text = render(analysis, write_narrative(analysis, None, "m"), "r.json", "refreshed")
    for number, title in enumerate(
        [
            "Summary",
            "Setup",
            "Headline result (from Flagsmith)",
            "Reconciliation",
            "Split health",
            "Breakdowns (ledger)",
            "Traffic",
            "Limitations",
            "Decision",
        ],
        start=1,
    ):
        assert f"## {number}. {title}" in text
    assert "Exploratory: not pre-registered as decision criteria; small samples." in text
    assert "fewer than 20 scored sessions" in text
    assert "Prose in sections 1 and 9: fixed template (no model used)" in text


# --- narrative ------------------------------------------------------------------------------


def test_number_check_rejects_numbers_not_in_the_input() -> None:
    source = {"exposed": {"control": "23"}, "mean": "4.40", "rule": "at least 75"}
    assert unsupported_numbers("Control had 23 sessions, mean 4.4, rule 75.", source) == []
    assert unsupported_numbers("Control had 24 sessions and 95% confidence.", source) == [
        "24",
        "95",
    ]


def test_model_draft_with_an_invented_number_falls_back(
    spec: ExperimentSpec, state: RunState
) -> None:
    analysis = run(spec, state, results_json())
    sent: list[dict[str, Any]] = []

    def drafter(source: dict[str, Any]) -> str:
        sent.append(source)
        return json.dumps({"summary": "Prescriptive cut calls by 37%.", "decision": "Wait."})

    written = write_narrative(analysis, drafter, "gemini-x")
    assert written.narrative == template(analysis)
    assert "numbers not in its input: 37" in written.author
    assert sent == [facts(analysis)]


def test_model_draft_with_only_input_numbers_is_used(spec: ExperimentSpec, state: RunState) -> None:
    analysis = run(spec, state, results_json())

    def drafter(source: dict[str, Any]) -> str:
        return json.dumps(
            {"summary": "Each arm has 3 exposed sessions of 75 needed.", "decision": "Wait."}
        )

    written = write_narrative(analysis, drafter, "gemini-x")
    assert written.narrative.summary == "Each arm has 3 exposed sessions of 75 needed."
    assert written.author.startswith("drafted by gemini-x")


@pytest.mark.parametrize("answer", ["not json", json.dumps({"summary": "only one"})])
def test_malformed_draft_falls_back(spec: ExperimentSpec, state: RunState, answer: str) -> None:
    analysis = run(spec, state, results_json())
    written = write_narrative(analysis, lambda source: answer, "gemini-x")
    assert written.narrative == template(analysis)
    assert "malformed" in written.author


def test_failing_model_call_falls_back(spec: ExperimentSpec, state: RunState) -> None:
    analysis = run(spec, state, results_json())

    def drafter(source: dict[str, Any]) -> str:
        raise ConnectionError("down")

    written = write_narrative(analysis, drafter, "gemini-x")
    assert written.narrative == template(analysis)
    assert "model call failed: ConnectionError" in written.author


GUARDRAIL_ONLY = {
    PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97),  # the variant wins the primary metric
    WRONG: _inference(0.8, 0.2, 1.4, 0.01),  # but wrong bookings are credibly up
}
CONTROL_WINS = {PRIMARY: _inference(0.3, 0.1, 0.5, 0.02)}


def test_keep_control_says_why_control_is_kept(spec: ExperimentSpec, state: RunState) -> None:
    guarded = run(spec, state, results_json(n=(75, 75), inference=GUARDRAIL_ONLY))
    assert guarded.decision.decision is Decision.KEEP_CONTROL
    assert facts(guarded)["keep_control_reason"] == "guardrail_credibly_worse"
    assert facts(guarded)["guardrails_credibly_worse"] == ["Wrong booking"]
    prose = template(guarded).decision
    assert "credibly worse on the guardrail Wrong booking" in prose
    assert "does not make control the winner" in prose
    assert "Control met" not in prose
    lost = run(spec, state, results_json(n=(75, 75), inference=CONTROL_WINS))
    assert facts(lost)["keep_control_reason"] == "control_meets_threshold"
    assert facts(lost)["guardrails_credibly_worse"] == []
    assert template(lost).decision.startswith("Control met the win-probability threshold")
    shipped = run(
        spec,
        state,
        results_json(
            n=(75, 75), inference=CONTROL_WINS | {PRIMARY: _inference(-0.4, -0.6, -0.2, 0.97)}
        ),
    )
    assert "keep_control_reason" not in facts(shipped)
    assert "'guardrail_credibly_worse'" in NARRATIVE_INSTRUCTIONS
    assert "do not call control the winner" in NARRATIVE_INSTRUCTIONS


def test_template_numbers_are_all_in_the_facts(spec: ExperimentSpec, state: RunState) -> None:
    for now in (NOW, AFTER_END):
        analysis = run(spec, state, results_json(), now=now)
        prose = template(analysis)
        assert unsupported_numbers(f"{prose.summary} {prose.decision}", facts(analysis)) == []


# --- refresh and save -----------------------------------------------------------------------


class FakeAdmin(FlagsmithAdmin):
    """Flagsmith results endpoints only."""

    def __init__(self, raw: dict[str, Any], refused: bool = False) -> None:
        super().__init__("second-audience", executable="flagsmith")
        self.raw = raw
        self.refused = refused
        self.refreshes = 0
        self.after_refresh: dict[str, Any] | None = None

    def get_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
        return {"id": experiment_id, "status": "running"}

    def get_results(self, env: str, experiment_id: int) -> dict[str, Any]:
        return self.raw

    def refresh_results(self, env: str, experiment_id: int) -> None:
        if self.refused:
            raise FlagsmithAdminError(
                ["api"], '{"detail":"A refresh was requested recently. Try again later."}'
            )
        self.refreshes += 1
        if self.after_refresh is not None:
            self.raw = self.after_refresh


def test_refresh_saves_new_results(tmp_path: Path) -> None:
    admin = FakeAdmin(results_json(as_of="2026-11-26T10:00:00Z"))
    admin.after_refresh = results_json(as_of="2026-11-26T11:00:00Z")
    saved = refresh_and_save(admin, ENV, 680, tmp_path, NOW, sleep=lambda s: None)
    assert saved.refreshed
    assert admin.refreshes == 1
    assert saved.path.name == "results-20261127T060000Z.json"
    assert json.loads(saved.path.read_text(encoding="utf-8")) == admin.after_refresh


def test_refused_refresh_reuses_the_saved_file(tmp_path: Path) -> None:
    raw = results_json(as_of="2026-11-26T10:00:00Z")
    previous = tmp_path / "results-20261126T100000Z.json"
    previous.write_text(json.dumps(raw), encoding="utf-8")
    saved = refresh_and_save(FakeAdmin(raw, refused=True), ENV, 680, tmp_path, NOW)
    assert not saved.refreshed
    assert saved.path == previous
    assert "refused" in saved.note
    assert sorted(p.name for p in tmp_path.iterdir()) == [previous.name]


def test_refresh_that_never_lands_is_reported(tmp_path: Path) -> None:
    sleeps: list[float] = []
    admin = FakeAdmin(results_json(as_of="2026-11-26T10:00:00Z"))
    saved = refresh_and_save(admin, ENV, 680, tmp_path, NOW, sleep=sleeps.append)
    assert not saved.refreshed
    assert "not ready" in saved.note
    assert len(sleeps) == 15


def test_other_refresh_errors_propagate(tmp_path: Path) -> None:
    class Broken(FakeAdmin):
        def refresh_results(self, env: str, experiment_id: int) -> None:
            raise FlagsmithAdminError(["api"], "Error: 403 Forbidden")

    with pytest.raises(FlagsmithAdminError):
        refresh_and_save(Broken(results_json()), ENV, 680, tmp_path, NOW)


# --- the CLI --------------------------------------------------------------------------------


class FakeRepo:
    """Only the approver name is needed for `--peek`."""

    def user_name(self) -> str:
        return "dev"


class Workspace:
    """A working directory with the spec, its state file and a seeded SQLite ledger."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]) -> None:
        (root / "experiments" / ".state").mkdir(parents=True)
        shutil.copy(SPEC_SOURCE, root / "experiments" / SPEC_SOURCE.name)
        (root / "experiments" / ".state" / f"{SLUG}.json").write_text(
            json.dumps({**STATE, "started_at": "2020-01-01T00:00:00Z"}), encoding="utf-8"
        )
        monkeypatch.chdir(root)
        self.database_url = f"sqlite:///{root / 'ledger.db'}"
        self.ledger = Ledger(create_engine(self.database_url))
        self._seed()
        self.admin = FakeAdmin(raw)
        self.drafts: list[dict[str, Any]] = []
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            sa_database_url=SecretStr(self.database_url),
        )
        context = cli.Context(
            load_settings=lambda: settings,
            make_admin=lambda s: self.admin,
            checks=lambda *args: pytest.fail("no doctor checks in status or report"),
            open_repo=lambda path: FakeRepo(),  # type: ignore[arg-type,return-value]
            open_ledger=lambda s: self.ledger,
            now=lambda: NOW,
            make_drafter=lambda s: self._draft,
            sleep=lambda seconds: None,
            make_proposer=lambda s: None,
            smoke=lambda s: pytest.fail("no smoke session here"),
        )
        monkeypatch.setattr(cli, "make_context", lambda: context)

    def _draft(self, source: dict[str, Any]) -> str:
        self.drafts.append(source)
        return json.dumps({"summary": "A drafted summary.", "decision": "A drafted decision."})

    def _seed(self) -> None:
        """Three control and three prescriptive sessions, as `results_json()` counts them."""
        for index, (variant, final, calls) in enumerate(
            [("control", "correct", 5)] * 3
            + [("prescriptive", "correct", 3), ("prescriptive", "incorrect", 3)]
            + [("prescriptive", "no_booking", 3)]
        ):
            sid = f"session{index}"
            self.ledger.start_session(sid, "s01", "aligned", "lab", "gemini-a", None)
            self.ledger.record_exposure(sid, variant, in_experiment=True)
            self.ledger.finish_session(sid, final, calls, final_state=final)

    def audit_rows(self) -> list[Any]:
        engine = create_engine(self.database_url)
        with engine.connect() as conn:
            return list(conn.execute(select(audit_log)))


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli.app, list(args))


def test_status_before_the_rule_shows_progress_and_no_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = results_json(inference={PRIMARY: _inference(-0.4, -0.6, -0.2, 0.999)})
    Workspace(tmp_path, monkeypatch, raw)
    result = invoke("status", SLUG)
    assert result.exit_code == 0, result.output
    assert "running" in result.output
    assert "control 3/75, prescriptive 3/75" in result.output
    assert "Stopping rule: not met" in result.output
    assert "2d 0h 30m left" in result.output
    assert "lab 6 sessions, 6 in experiment" in result.output
    assert "0.999" not in result.output
    assert "win probability 0" not in result.output


def test_status_peek_warns_and_writes_an_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = results_json(inference={PRIMARY: _inference(-0.4, -0.6, -0.2, 0.999)})
    workspace = Workspace(tmp_path, monkeypatch, raw)
    result = invoke("status", SLUG, "--peek")
    assert result.exit_code == 0, result.output
    assert "WARNING: peeking" in result.output
    assert "win probability 0.999" in result.output
    (row,) = workspace.audit_rows()
    assert (row.action, row.experiment_slug, row.approved_by) == ("peek", SLUG, "dev")
    assert json.loads(row.detail)["exposures"] == {"control": 3, "prescriptive": 3}


def test_report_no_llm_writes_report_and_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json())
    result = invoke("report", SLUG, "--no-llm")
    assert result.exit_code == 0, result.output
    folder = tmp_path / "reports" / SLUG
    (saved,) = folder.glob("results-*.json")
    assert json.loads(saved.read_text(encoding="utf-8")) == results_json()
    text = (folder / "report.md").read_text(encoding="utf-8")
    assert "## 9. Decision" in text
    assert "fixed template (no model used)" in text
    assert "| Exposed sessions | control | 3 | 3 | 3 | 0 | match |" in text
    assert workspace.drafts == []


def test_report_uses_a_checked_model_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json())
    result = invoke("report", SLUG)
    assert result.exit_code == 0, result.output
    text = (tmp_path / "reports" / SLUG / "report.md").read_text(encoding="utf-8")
    assert "A drafted summary." in text
    assert "A drafted decision." in text
    assert "drafted by gemini-3.5-flash-lite" in text
    assert len(workspace.drafts) == 1


def test_status_without_a_started_experiment_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Workspace(tmp_path, monkeypatch, results_json())
    result = invoke("status", "never-started")
    assert result.exit_code == 1
    assert "has `sa start` run?" in result.output
