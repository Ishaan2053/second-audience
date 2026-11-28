"""`sa ship`: the plan, the read-back checks, the smoke session and the CLI, Flagsmith faked."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastmcp.utilities.tests import asgi_client
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from typer.testing import CliRunner

from second_audience.booking.server import build_server
from second_audience.booking.sessions import SessionManager
from second_audience.config import Settings
from second_audience.core.experiment import NullExperimentClient
from second_audience.core.ledger import Ledger, audit_log
from second_audience.core.styles import ARM_FLAG_VALUES, CONTROL, PRESCRIPTIVE, ErrorStyle
from second_audience.operator import cli
from second_audience.operator.analysis import Analysis, Decision, RunState, analyse
from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError
from second_audience.operator.results import ExperimentResults
from second_audience.operator.ship import (
    SMOKE_ATTEMPTS,
    SMOKE_WAIT_SECONDS,
    Outcome,
    ShipRefusedError,
    audit_detail,
    complete,
    decision_record,
    observe_style,
    plan_ship,
    read_back,
    served_value,
    smoke_until,
)
from second_audience.operator.spec import load_spec

SPEC_SOURCE = Path(__file__).parents[1] / "experiments" / "error-style-v1.yaml"
SLUG = "error-style-v1"
ENV = "envkey"
PRIMARY, COMPLETED, WRONG = 665, 666, 667
FEATURE_ID, VARIANT_ID = 1, 2
NOW = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)  # before the hard end
STATE = {
    "slug": SLUG,
    "environment": "production",
    "environment_key": ENV,
    "flag": "error_style",
    "feature_id": FEATURE_ID,
    "variant_ids": {"prescriptive": VARIANT_ID},
    "metric_ids": {
        "Tool calls per session": PRIMARY,
        "Task completed correctly": COMPLETED,
        "Wrong booking": WRONG,
    },
    "experiment_id": 680,
    "started_at": "2020-01-01T00:00:00Z",
    "commit": "abc123",
    "approved_by": "dev",
    "recorded_at": "2026-11-26T15:00:01+00:00",
}

WIN = {PRIMARY: (-0.4, -0.6, -0.2, 0.99)}
LOSS = {PRIMARY: (0.3, 0.1, 0.5, 0.01)}
UNCLEAR = {PRIMARY: (-0.1, -0.3, 0.1, 0.80)}
GUARDRAIL_HIT = {**WIN, WRONG: (0.8, 0.2, 1.4, 0.01)}


def results_json(
    n: int = 75,
    inference: dict[int, tuple[float, float, float, float]] | None = None,
    as_of: str = "2026-11-27T05:00:00Z",
) -> dict[str, Any]:
    """A results payload (docs/spike-findings.md, question 5) with `n` exposed per arm."""
    inference = inference or {}
    metrics = []
    for metric_id in (PRIMARY, COMPLETED, WRONG):
        found = inference.get(metric_id)
        metrics.append(
            {
                "metric_id": metric_id,
                "variants": {
                    "control": {"n": n, "sum": 4.0 * n, "sum_squares": 0.0},
                    "prescriptive": {"n": n, "sum": 3.0 * n, "sum_squares": 0.0},
                },
                "inference": {
                    "prescriptive": dict(
                        zip(("lift", "ci_low", "ci_high", "chance_to_win"), found, strict=True)
                    )
                    if found
                    else None
                },
            }
        )
    return {
        "results": {
            "as_of": as_of,
            "is_final": False,
            "payload": {"srm_p_value": None, "metrics": metrics},
        }
    }


def analysis_for(raw: dict[str, Any], tmp_path: Path, now: datetime = NOW) -> Analysis:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(STATE), encoding="utf-8")
    spec, state = load_spec(SPEC_SOURCE), RunState.load(path)
    return analyse(spec, state, ExperimentResults.from_api(raw), {}, [], now)


RUNNING = {"id": 680, "status": "running"}
OLD = "2026-11-26T12:00:00Z"  # 18 hours before NOW


# --- the plan -------------------------------------------------------------------------------


def test_ship_variant_plan_rolls_out_the_winning_option(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r.json"))
    assert plan.decision is Decision.SHIP_VARIANT
    assert (plan.rollout_decision, plan.rollout_variant) == ("variant", VARIANT_ID)
    assert plan.serve_value == ARM_FLAG_VALUES["prescriptive"]
    changes = plan.changes()
    assert '{"rollout_decision": "variant", "rollout_variant": 2}' in changes[0]
    assert "serves prescriptive to everyone" in changes[1]


@pytest.mark.parametrize(
    ("inference", "decision"),
    [(LOSS, Decision.KEEP_CONTROL), (UNCLEAR, Decision.INCONCLUSIVE),
     (GUARDRAIL_HIT, Decision.KEEP_CONTROL)],
)  # fmt: skip
def test_other_decisions_end_with_control_and_leave_the_flag(
    tmp_path: Path, inference: dict[int, tuple[float, float, float, float]], decision: Decision
) -> None:
    plan = plan_ship(analysis_for(results_json(inference=inference), tmp_path), RUNNING, Path("r"))
    assert plan.decision is decision
    assert (plan.rollout_decision, plan.rollout_variant) == ("control", None)
    assert plan.serve_value == ARM_FLAG_VALUES["control"]
    assert "unchanged" in plan.changes()[1]


def test_plan_refuses_before_the_stopping_rule(tmp_path: Path) -> None:
    analysis = analysis_for(results_json(n=74, inference=WIN), tmp_path)
    with pytest.raises(ShipRefusedError, match="stopping rule is not met"):
        plan_ship(analysis, RUNNING, Path("r.json"))


def test_plan_refuses_an_experiment_that_ended_otherwise(tmp_path: Path) -> None:
    analysis = analysis_for(results_json(inference=WIN), tmp_path)
    for ended in (
        {"status": "completed", "ended_at": "x", "rollout_decision": "control"},
        {"status": "completed", "rollout_decision": "variant", "rollout_variant": 99},
        {"status": "completed", "rollout_decision": "variant", "rollout_variant": {"x": 1}},
        {"status": "completed", "rollout_decision": "variant", "rollout_variant": None},
        {"status": "completed", "rollout_decision": "variant"},
    ):
        with pytest.raises(ShipRefusedError, match=r"already ended.*not this plan's 'variant'"):
            plan_ship(analysis, ended, Path("r.json"))
    with pytest.raises(ShipRefusedError, match="not running"):
        plan_ship(analysis, {"status": "created"}, Path("r.json"))
    early = analysis_for(results_json(n=40, inference=WIN), tmp_path)
    ended = {"status": "completed", "rollout_decision": "variant"}
    with pytest.raises(ShipRefusedError, match=r"already ended.*stopping rule is not met"):
        plan_ship(early, ended, Path("r.json"))


def test_a_control_plan_refuses_a_non_null_rollout_variant(tmp_path: Path) -> None:
    analysis = analysis_for(results_json(inference=LOSS), tmp_path)
    for variant in (VARIANT_ID, {"x": 1}, "unreadable"):
        ended = {"status": "completed", "rollout_decision": "control", "rollout_variant": variant}
        with pytest.raises(ShipRefusedError, match="not this plan's 'control'"):
            plan_ship(analysis, ended, Path("r.json"))
    for ended in (
        {"status": "completed", "rollout_decision": "control", "rollout_variant": None},
        {"status": "completed", "rollout_decision": "control"},
    ):
        assert plan_ship(analysis, ended, Path("r.json")).record_only


@pytest.mark.parametrize("variant", [VARIANT_ID, {"id": VARIANT_ID}])
def test_plan_records_only_an_experiment_that_ended_as_planned(
    tmp_path: Path, variant: Any
) -> None:
    analysis = analysis_for(results_json(inference=WIN), tmp_path)
    ended = {"status": "completed", "rollout_decision": "variant", "rollout_variant": variant}
    plan = plan_ship(analysis, ended, Path("r.json"))
    assert plan.record_only
    assert plan.changes() == [
        "none: experiment error-style-v1 already ended with rollout_decision 'variant', as "
        "planned; complete/ is not called again. This run reads back, smoke-tests and records "
        "the decision."
    ]


def test_fresh_results_give_no_warning(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    assert plan.warnings == ()


def test_old_results_are_flagged(tmp_path: Path) -> None:
    later = datetime(2026, 11, 27, 11, 1, tzinfo=UTC)  # 6 h 1 min after as_of
    analysis = analysis_for(results_json(inference=WIN), tmp_path, later)
    assert plan_ship(analysis, RUNNING, Path("r")).warnings == (
        "the saved results are 6.0 hours old (as of 2026-11-27T05:00:00+00:00), older than 6 hours",
    )


def test_results_from_before_the_hard_end_are_flagged_when_only_time_met_the_rule(
    tmp_path: Path,
) -> None:
    after_end = datetime(2026, 11, 29, 7, 0, tzinfo=UTC)  # hard end 06:30 UTC
    before = results_json(n=40, inference=WIN, as_of="2026-11-29T06:00:00Z")
    plan = plan_ship(analysis_for(before, tmp_path, after_end), RUNNING, Path("r"))
    assert len(plan.warnings) == 1
    assert "met only because the hard end" in plan.warnings[0]
    assert "run `sa report error-style-v1`" in plan.warnings[0]
    after = results_json(n=40, inference=WIN, as_of="2026-11-29T06:45:00Z")
    assert plan_ship(analysis_for(after, tmp_path, after_end), RUNNING, Path("r")).warnings == ()
    full = results_json(inference=WIN, as_of="2026-11-29T06:00:00Z")  # enrolment met the rule
    assert plan_ship(analysis_for(full, tmp_path, after_end), RUNNING, Path("r")).warnings == ()


# --- reading back -----------------------------------------------------------------------------


def _feature_state(value: str, allocation: float, enabled: bool = True) -> dict[str, Any]:
    return {
        "feature_state_value": value,
        "enabled": enabled,
        "multivariate_feature_state_values": [
            {"multivariate_feature_option": VARIANT_ID, "percentage_allocation": allocation}
        ],
    }


def test_served_value_reads_a_full_rollout_the_default_or_a_split() -> None:
    options = {VARIANT_ID: "variant"}
    assert served_value(_feature_state("default", 100.0), options) == "variant"
    assert served_value(_feature_state("default", 0.0), options) == "default"
    assert served_value(_feature_state("default", 50.0), options) is None


def test_served_value_reads_a_nested_option() -> None:
    state = _feature_state("default", 100.0)
    state["multivariate_feature_state_values"][0]["multivariate_feature_option"] = {
        "id": VARIANT_ID,
        "value": "variant",
    }
    assert served_value(state, {VARIANT_ID: "variant"}) == "variant"


class FakeAdmin(FlagsmithAdmin):
    """Flagsmith's experiment and feature-state endpoints; `complete` acts like the API."""

    def __init__(self, raw: dict[str, Any], status: str = "running") -> None:
        super().__init__("second-audience", executable="flagsmith")
        self.raw = raw
        self.experiment: dict[str, Any] = {"id": 680, "status": status, "ended_at": None}
        self.state = _feature_state(ARM_FLAG_VALUES["control"], 0.0)
        self.changes: list[tuple[str, int | None]] = []
        self.ignore_complete = False
        self.read_error: Exception | None = None

    def get_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
        if self.changes and self.read_error is not None:
            raise self.read_error
        return dict(self.experiment)

    def get_results(self, env: str, experiment_id: int) -> dict[str, Any]:
        return self.raw

    def complete_experiment(
        self, env: str, experiment_id: int, rollout_decision: str, rollout_variant: int | None
    ) -> dict[str, Any]:
        self.changes.append((rollout_decision, rollout_variant))
        if self.ignore_complete:
            return self.experiment
        self.experiment.update(
            status="completed",
            ended_at="2026-11-29T07:00:00Z",
            rollout_decision=rollout_decision,
            rollout_variant=rollout_variant,
        )
        if rollout_decision == "variant":
            self.state = _feature_state(ARM_FLAG_VALUES["control"], 100.0)
        return self.experiment

    def feature_state(self, env: str, feature_id: int) -> dict[str, Any]:
        assert feature_id == FEATURE_ID
        return self.state


def test_complete_then_read_back_verifies(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    outcome = complete(plan, admin)
    assert admin.changes == [("variant", VARIANT_ID)]
    assert not outcome.read_back
    read_back(plan, admin, outcome)
    assert outcome.read_back
    assert outcome.smoke_safe
    assert outcome.served == ARM_FLAG_VALUES["prescriptive"]
    assert outcome.problems == []


def test_read_back_reports_what_does_not_match(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    admin.ignore_complete = True
    outcome = complete(plan, admin)
    read_back(plan, admin, outcome)
    assert len(outcome.problems) == 3  # status, rollout_decision, served value
    assert not outcome.smoke_safe


def test_an_answer_that_is_not_an_object_is_a_problem_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    monkeypatch.setattr(admin, "complete_experiment", lambda *args: [1])
    outcome = complete(plan, admin)
    assert outcome.experiment == {}
    assert outcome.problems[0].startswith("complete/ answered with an unreadable shape")


def test_unreadable_feature_state_is_a_problem_not_an_exception(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    outcome = complete(plan, admin)
    admin.state = {"enabled": True, "multivariate_feature_state_values": [{"id": 5}]}
    read_back(plan, admin, outcome)
    assert outcome.problems == ["read-back failed: KeyError: 'multivariate_feature_option'"]
    assert outcome.smoke_safe  # the experiment itself was read back as completed


@pytest.mark.parametrize(
    "error",
    [
        FlagsmithAdminError(["api"], "expected one environment default for feature 1, got 2"),
        json.JSONDecodeError("Expecting value", "", 0),
    ],
)
def test_read_back_failure_is_a_problem_not_an_exception(tmp_path: Path, error: Exception) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    admin.read_error = error
    outcome = complete(plan, admin)
    read_back(plan, admin, outcome)
    assert not outcome.read_back
    assert not outcome.smoke_safe
    assert outcome.problems[0].startswith(f"read-back failed: {type(error).__name__}")


# --- the smoke session ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("style", "variant"), [(CONTROL, "control"), (PRESCRIPTIVE, "prescriptive")]
)
async def test_observe_style_reads_the_served_style(
    tmp_path: Path, style: ErrorStyle, variant: str
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'smoke.db'}")
    manager = SessionManager(
        Ledger(engine),
        NullExperimentClient(style=style, variant=variant),
        max_tool_calls=12,
        idle_seconds=600,
    )
    server = build_server(manager, Settings(_env_file=None, sa_env="test"))  # type: ignore[call-arg]
    async with asgi_client(server) as client:
        assert await observe_style(client) == style
    engine.dispose()


def test_smoke_until_waits_for_propagation_before_every_probe() -> None:
    seen = iter([CONTROL, CONTROL, PRESCRIPTIVE])
    events: list[str] = []

    def probe() -> ErrorStyle:
        events.append("probe")
        return next(seen)

    def sleep(seconds: float) -> None:
        assert seconds == SMOKE_WAIT_SECONDS
        events.append("sleep")

    assert smoke_until(probe, PRESCRIPTIVE, sleep) == PRESCRIPTIVE
    assert events == ["sleep", "probe"] * 3  # the first probe also waits
    sleeps: list[float] = []
    assert smoke_until(lambda: CONTROL, PRESCRIPTIVE, sleeps.append) == CONTROL
    assert len(sleeps) == SMOKE_ATTEMPTS


def test_decision_record_names_the_approver_and_failures(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r.json"))
    outcome = Outcome({"status": "completed", "ended_at": "t"}, "v", None, ["smoke failed"])
    outcome.read_back = outcome.checked = True
    text = decision_record(plan, outcome, "dev", NOW)
    assert text.startswith("## Decision (2026-11-27 06:00 UTC)")
    assert "approved by dev (typed `ship error-style-v1`)" in text
    assert "**Verification failed:**" in text
    assert "pending" not in text


def test_decision_record_before_verification_says_it_is_pending(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r.json"))
    text = decision_record(plan, Outcome({"status": "completed"}), "dev", NOW)
    assert "**Verification pending:**" in text
    assert "Verified:" not in text
    assert "now serves" not in text


def test_pending_record_on_the_record_only_path_does_not_claim_a_change(tmp_path: Path) -> None:
    analysis = analysis_for(results_json(inference=WIN), tmp_path)
    ended = {"status": "completed", "rollout_decision": "variant", "rollout_variant": VARIANT_ID}
    plan = plan_ship(analysis, ended, Path("r.json"))
    text = decision_record(plan, Outcome(ended), "dev", NOW)
    assert "**Verification pending:** Flagsmith reported the experiment already ended" in text
    assert "accepted the change" not in text


def test_audit_detail_reports_ledger_counts_when_a_guardrail_has_no_inference(
    tmp_path: Path,
) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    detail = audit_detail(plan, Outcome({}))
    assert detail["verification"] == "pending"
    assert set(detail["guardrail_ledger_counts"]) == {"Task completed correctly", "Wrong booking"}
    assert detail["guardrail_ledger_counts"]["Wrong booking"]["control"] == {
        "scored": 0,
        "count": 0,
    }
    assert detail["trap_wrong_bookings"] == []
    covered = {**WIN, COMPLETED: (0.0, -0.1, 0.1, 0.5), WRONG: (0.0, -0.1, 0.1, 0.5)}
    plan = plan_ship(analysis_for(results_json(inference=covered), tmp_path), RUNNING, Path("r"))
    assert "guardrail_ledger_counts" not in audit_detail(plan, Outcome({}))


# --- the CLI ----------------------------------------------------------------------------------


class FakeRepo:
    def user_name(self) -> str:
        return "dev"


class Workspace:
    """Spec, state, saved results and a SQLite ledger in a temporary working directory."""

    def __init__(
        self,
        root: Path,
        monkeypatch: pytest.MonkeyPatch,
        raw: dict[str, Any],
        *,
        served: ErrorStyle = PRESCRIPTIVE,
        save: bool = True,
    ) -> None:
        (root / "experiments" / ".state").mkdir(parents=True)
        shutil.copy(SPEC_SOURCE, root / "experiments" / SPEC_SOURCE.name)
        (root / "experiments" / ".state" / f"{SLUG}.json").write_text(
            json.dumps(STATE), encoding="utf-8"
        )
        self.folder = root / "reports" / SLUG
        self.folder.mkdir(parents=True)
        (self.folder / "report.md").write_text("# Report: error-style-v1\n", encoding="utf-8")
        if save:
            (self.folder / "results-20261127T050000Z.json").write_text(
                json.dumps(raw), encoding="utf-8"
            )
        monkeypatch.chdir(root)
        self.database_url = f"sqlite:///{root / 'ledger.db'}"
        self.ledger = Ledger(create_engine(self.database_url))
        self.admin = FakeAdmin(raw)
        self.smokes = 0
        self.served = served
        self.smoke_error: BaseException | None = None
        self.settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            sa_database_url=SecretStr(self.database_url),
            sa_server_url="http://server/mcp",
            sa_lab_key=SecretStr("lab"),
        )
        context = cli.Context(
            load_settings=lambda: self.settings,
            make_admin=lambda s: self.admin,
            checks=lambda *args: pytest.fail("sa ship runs no doctor checks"),
            open_repo=lambda path: FakeRepo(),  # type: ignore[arg-type,return-value]
            open_ledger=lambda s: self.ledger,
            now=lambda: NOW,
            make_drafter=lambda s: None,
            sleep=lambda seconds: None,
            make_proposer=lambda s: None,
            smoke=self._smoke,
        )
        monkeypatch.setattr(cli, "make_context", lambda: context)

    def _smoke(self, settings: Settings) -> ErrorStyle:
        self.smokes += 1
        if self.smoke_error is not None:
            raise self.smoke_error
        return self.served

    def audit_rows(self) -> list[Any]:
        with create_engine(self.database_url).connect() as conn:
            return list(conn.execute(select(audit_log)))

    def unchanged(self) -> bool:
        return (
            self.admin.changes == []
            and self.smokes == 0
            and self.audit_rows() == []
            and not (self.folder / "decision.md").exists()
        )


def invoke(*args: str, answer: str | None = None) -> Any:
    return CliRunner().invoke(cli.app, list(args), input=answer)


def test_ship_refuses_before_the_stopping_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(n=40, inference=WIN))
    for args in (["ship", SLUG], ["ship", SLUG, "--dry-run"]):
        result = invoke(*args, answer=f"ship {SLUG}\n")
        assert result.exit_code == 1
        assert "refused: the stopping rule is not met" in result.output
        assert "0.990" not in result.output
    assert workspace.unchanged()


def test_ship_needs_saved_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN), save=False)
    result = invoke("ship", SLUG)
    assert result.exit_code == 1
    assert f"run `sa report {SLUG}` first" in result.output
    assert workspace.unchanged()


def test_dry_run_prints_the_decision_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    result = invoke("ship", SLUG, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "Rule (HYPOTHESIS.md section 10)" in result.output
    assert "primary Tool calls per session: control n=75 mean=4.000" in result.output
    assert "win probability 0.990" in result.output
    assert "Decision: ship_variant" in result.output
    assert '"rollout_decision": "variant", "rollout_variant": 2' in result.output
    assert "Dry run: nothing was changed." in result.output
    assert workspace.unchanged()


def test_wrong_confirmation_aborts_without_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    result = invoke("ship", SLUG, answer=f"{SLUG}\n")  # the slug alone is not the phrase
    assert result.exit_code == 1
    assert "confirmation did not match; nothing was changed" in result.output
    assert workspace.unchanged()


def test_ship_ends_verifies_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 0, result.output
    assert workspace.admin.changes == [("variant", VARIANT_ID)]
    assert workspace.smokes == 1
    first, row = workspace.audit_rows()
    assert (first.action, first.experiment_slug, first.approved_by) == ("ship", SLUG, "dev")
    assert json.loads(first.detail)["verification"] == "pending"
    assert (row.action, row.experiment_slug, row.approved_by) == ("ship_verify", SLUG, "dev")
    detail = json.loads(row.detail)
    assert detail["verification"] == "finished"
    assert detail["record_only"] is False
    assert detail["warnings"] == []
    assert detail["decision"] == "ship_variant"
    assert detail["inference"]["Tool calls per session"]["chance_to_win"] == 0.99
    assert detail["served_value"] == ARM_FLAG_VALUES["prescriptive"]
    assert detail["problems"] == []
    report = (workspace.folder / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# Report: error-style-v1\n\n## Decision (2026-11-27 06:00 UTC)")
    assert "Decision: **ship_variant**" in report
    assert "Verified: experiment ended" in report
    assert "pending" not in report
    assert report.count("## Decision") == 1  # the final record replaced the pending one
    assert (workspace.folder / "decision.md").read_text(encoding="utf-8") in report


def test_keep_control_ends_the_experiment_and_leaves_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=LOSS), served=CONTROL)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 0, result.output
    assert workspace.admin.changes == [("control", None)]
    _, row = workspace.audit_rows()
    assert json.loads(row.detail)["decision"] == "keep_control"


def test_failed_verification_is_recorded_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN), served=CONTROL)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "verification failed" in result.output
    assert workspace.smokes == SMOKE_ATTEMPTS
    _, row = workspace.audit_rows()
    assert json.loads(row.detail)["problems"]
    assert json.loads(row.detail)["verification"] == "failed"


def test_read_back_failure_after_completion_is_recorded_and_skips_the_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.admin.read_error = FlagsmithAdminError(["api"], "expected one environment default")
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a clean exit, not a traceback
    assert workspace.admin.changes == [("variant", VARIANT_ID)]
    assert workspace.smokes == 0
    first, row = workspace.audit_rows()
    assert (first.action, row.action) == ("ship", "ship_verify")
    problems = json.loads(row.detail)["problems"]
    assert problems[0].startswith("read-back failed: FlagsmithAdminError")
    assert "smoke session skipped" in problems[1]
    record = (workspace.folder / "decision.md").read_text(encoding="utf-8")
    assert "**Verification failed:**" in record
    assert "not read back" in record


def test_smoke_is_skipped_unless_the_experiment_reads_back_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.admin.ignore_complete = True  # Flagsmith still reports it running
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert workspace.smokes == 0
    _, row = workspace.audit_rows()
    assert any("smoke session skipped" in p for p in json.loads(row.detail)["problems"])


def test_interrupted_smoke_still_writes_the_final_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.smoke_error = KeyboardInterrupt()
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    _, row = workspace.audit_rows()
    assert json.loads(row.detail)["problems"] == ["interrupted before verification finished"]
    assert "interrupted" in (workspace.folder / "decision.md").read_text(encoding="utf-8")


def test_ship_needs_a_lab_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.settings = workspace.settings.model_copy(update={"sa_lab_key": None})
    for args in (["ship", SLUG], ["ship", SLUG, "--dry-run"]):
        result = invoke(*args, answer=f"ship {SLUG}\n")
        assert result.exit_code == 1
        assert "SA_LAB_KEY is not set" in result.output
    assert workspace.unchanged()


def test_dry_run_shows_guardrail_ledger_counts_without_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    result = invoke("ship", SLUG, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "ledger counts for Wrong booking (scored sessions): control 0 of 0" in result.output
    assert "prescriptive wrong bookings on trap scenarios (ledger): 0 (none)" in result.output


def test_ship_refuses_an_experiment_that_ended_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.admin.experiment.update(status="completed", rollout_decision="control")
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "already ended" in result.output
    assert workspace.unchanged()


def test_rerun_after_an_unrecorded_completion_records_without_ending_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.admin.complete_experiment(ENV, 680, "variant", VARIANT_ID)  # an earlier run
    workspace.admin.changes.clear()
    result = invoke("ship", SLUG, answer=f"{SLUG}\n")
    assert result.exit_code == 1
    assert "confirmation did not match" in result.output
    assert workspace.unchanged()
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 0, result.output
    assert "record this decision for the experiment that has already ended" in result.output
    assert workspace.admin.changes == []  # complete/ was not called again
    assert workspace.smokes == 1
    first, row = workspace.audit_rows()
    assert (first.action, row.action) == ("ship", "ship_verify")
    detail = json.loads(row.detail)
    assert (detail["record_only"], detail["verification"]) == (True, "finished")
    record = (workspace.folder / "decision.md").read_text(encoding="utf-8")
    assert "without calling complete/ again" in record
    assert "Verified: experiment ended" in record


@pytest.mark.parametrize("clear_audit_log", [False, True])
def test_rerun_after_a_verified_ship_is_refused_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clear_audit_log: bool
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    assert invoke("ship", SLUG, answer=f"ship {SLUG}\n").exit_code == 0
    if clear_audit_log:  # the final decision.md alone is enough to refuse
        with create_engine(workspace.database_url).begin() as conn:
            conn.execute(audit_log.delete())
    rows = workspace.audit_rows()
    record = (workspace.folder / "decision.md").read_text(encoding="utf-8")
    report = (workspace.folder / "report.md").read_text(encoding="utf-8")
    changes, smokes = list(workspace.admin.changes), workspace.smokes
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "has already been shipped and recorded" in result.output
    assert "nothing was changed" in result.output
    assert workspace.audit_rows() == rows
    assert (workspace.folder / "decision.md").read_text(encoding="utf-8") == record
    assert (workspace.folder / "report.md").read_text(encoding="utf-8") == report
    assert (workspace.admin.changes, workspace.smokes) == (changes, smokes)


def test_rerun_after_a_pending_only_record_completes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    real_read_back = cli.read_back

    def crash(*args: Any) -> None:
        raise RuntimeError("process died")

    monkeypatch.setattr(cli, "read_back", crash)  # stops right after the pending record
    assert invoke("ship", SLUG, answer=f"ship {SLUG}\n").exit_code == 1
    assert [r.action for r in workspace.audit_rows()] == ["ship"]
    pending = (workspace.folder / "decision.md").read_text(encoding="utf-8")
    assert "**Verification pending:**" in pending
    monkeypatch.setattr(cli, "read_back", real_read_back)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 0, result.output
    assert "record this decision for the experiment that has already ended" in result.output
    assert workspace.admin.changes == [("variant", VARIANT_ID)]  # complete/ only the first time
    assert [r.action for r in workspace.audit_rows()] == ["ship", "ship", "ship_verify"]
    report = (workspace.folder / "report.md").read_text(encoding="utf-8")
    assert report.count("## Decision") == 1  # the earlier pending record was replaced
    assert "Verified: experiment ended" in report
    assert "pending" not in report


def test_interrupted_complete_call_is_audited_as_an_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))

    def interrupted(*args: Any) -> dict[str, Any]:
        raise KeyboardInterrupt

    monkeypatch.setattr(workspace.admin, "complete_experiment", interrupted)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "interrupted during the complete/ call" in result.output
    (row,) = workspace.audit_rows()
    assert row.action == "ship_attempt"
    assert json.loads(row.detail)["error"] == "KeyboardInterrupt"
    assert not (workspace.folder / "decision.md").exists()


def test_failed_first_record_write_is_a_recorded_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    original = cli.append_record
    calls: list[str | None] = []

    def flaky(folder: Path, record: str, replacing: str | None = None) -> Path:
        calls.append(replacing)
        if len(calls) == 1:
            raise PermissionError("decision.md is locked")
        return original(folder, record, replacing)

    monkeypatch.setattr(cli, "append_record", flaky)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    _, row = workspace.audit_rows()
    assert json.loads(row.detail)["problems"] == [
        "decision record write (decision.md) failed: PermissionError: decision.md is locked"
    ]
    record = (workspace.folder / "decision.md").read_text(encoding="utf-8")
    assert "**Verification failed:**" in record
    assert "decision.md is locked" in record


def test_failed_complete_call_is_audited_as_an_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))

    def broken(*args: Any) -> dict[str, Any]:
        raise FlagsmithAdminError(["api", "complete"], "timed out")

    monkeypatch.setattr(workspace.admin, "complete_experiment", broken)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "the complete/ call failed" in result.output
    assert "audit log (ship_attempt)" in result.output
    (row,) = workspace.audit_rows()
    assert (row.action, row.approved_by) == ("ship_attempt", "dev")
    detail = json.loads(row.detail)
    assert detail["decision"] == "ship_variant"
    assert (detail["rollout_decision"], detail["rollout_variant"]) == ("variant", VARIANT_ID)
    assert "timed out" in detail["error"]
    assert not (workspace.folder / "decision.md").exists()
    assert workspace.smokes == 0


def test_stale_results_are_warned_in_the_plan_and_kept_in_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN, as_of=OLD))
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 0, result.output
    assert "Warning: the saved results are 18.0 hours old" in result.output
    _, row = workspace.audit_rows()
    assert json.loads(row.detail)["warnings"][0].startswith("the saved results are 18.0 hours")
    record = (workspace.folder / "decision.md").read_text(encoding="utf-8")
    assert "- **Warning:** the saved results are 18.0 hours old" in record


def test_unexpected_feature_state_after_completion_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    original = workspace.admin.complete_experiment

    def complete_then_odd_state(*args: Any) -> dict[str, Any]:
        answer = original(*args)
        workspace.admin.state = {"enabled": True, "multivariate_feature_state_values": [{}]}
        return answer

    monkeypatch.setattr(workspace.admin, "complete_experiment", complete_then_odd_state)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # recorded, not a traceback
    first, row = workspace.audit_rows()
    assert (first.action, row.action) == ("ship", "ship_verify")
    detail = json.loads(row.detail)
    assert detail["problems"][0] == "read-back failed: KeyError: 'multivariate_feature_option'"
    assert detail["verification"] == "failed"
    assert workspace.smokes == 1  # the experiment read back completed, so the smoke still ran


def test_report_keeps_the_decision_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    assert invoke("ship", SLUG, answer=f"ship {SLUG}\n").exit_code == 0
    workspace.admin.raw = results_json(inference=WIN)

    def refused(env: str, experiment_id: int) -> None:
        return None

    monkeypatch.setattr(workspace.admin, "refresh_results", refused)
    result = invoke("report", SLUG, "--no-llm")
    assert result.exit_code == 0, result.output
    report = (workspace.folder / "report.md").read_text(encoding="utf-8")
    assert "## 9. Decision" in report
    assert "The record below is the decision recorded at ship" in report
    assert "Decision recorded at ship from results `" in report
    assert report.rstrip().endswith(
        "- Verified: experiment ended, flag value and served style match."
    )
