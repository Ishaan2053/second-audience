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
from second_audience.operator.flagsmith_admin import FlagsmithAdmin
from second_audience.operator.results import ExperimentResults
from second_audience.operator.ship import (
    SMOKE_ATTEMPTS,
    Outcome,
    ShipRefusedError,
    apply_ship,
    decision_record,
    observe_style,
    plan_ship,
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
    n: int = 75, inference: dict[int, tuple[float, float, float, float]] | None = None
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
            "as_of": "2026-11-27T05:00:00Z",
            "is_final": False,
            "payload": {"srm_p_value": None, "metrics": metrics},
        }
    }


def analysis_for(raw: dict[str, Any], tmp_path: Path) -> Analysis:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(STATE), encoding="utf-8")
    spec, state = load_spec(SPEC_SOURCE), RunState.load(path)
    return analyse(spec, state, ExperimentResults.from_api(raw), {}, [], NOW)


RUNNING = {"id": 680, "status": "running"}


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


def test_plan_refuses_an_ended_experiment(tmp_path: Path) -> None:
    analysis = analysis_for(results_json(inference=WIN), tmp_path)
    ended = {"status": "completed", "ended_at": "x", "rollout_decision": "variant"}
    with pytest.raises(ShipRefusedError, match="already ended"):
        plan_ship(analysis, ended, Path("r.json"))
    with pytest.raises(ShipRefusedError, match="not running"):
        plan_ship(analysis, {"status": "created"}, Path("r.json"))


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


class FakeAdmin(FlagsmithAdmin):
    """Flagsmith's experiment and feature-state endpoints; `complete` acts like the API."""

    def __init__(self, raw: dict[str, Any], status: str = "running") -> None:
        super().__init__("second-audience", executable="flagsmith")
        self.raw = raw
        self.experiment: dict[str, Any] = {"id": 680, "status": status, "ended_at": None}
        self.state = _feature_state(ARM_FLAG_VALUES["control"], 0.0)
        self.changes: list[tuple[str, int | None]] = []
        self.ignore_complete = False

    def get_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
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
            status="completed", ended_at="2026-11-29T07:00:00Z", rollout_decision=rollout_decision
        )
        if rollout_decision == "variant":
            self.state = _feature_state(ARM_FLAG_VALUES["control"], 100.0)
        return self.experiment

    def feature_state(self, env: str, feature_id: int) -> dict[str, Any]:
        assert feature_id == FEATURE_ID
        return self.state


def test_apply_ship_ends_and_verifies(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    outcome = apply_ship(plan, admin)
    assert admin.changes == [("variant", VARIANT_ID)]
    assert outcome.served == ARM_FLAG_VALUES["prescriptive"]
    assert outcome.problems == []


def test_apply_ship_reports_what_does_not_match(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r"))
    admin = FakeAdmin(results_json(inference=WIN))
    admin.ignore_complete = True
    outcome = apply_ship(plan, admin)
    assert len(outcome.problems) == 3  # status, rollout_decision, served value


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


def test_smoke_until_waits_for_propagation() -> None:
    seen = iter([CONTROL, CONTROL, PRESCRIPTIVE])
    sleeps: list[float] = []
    assert smoke_until(lambda: next(seen), PRESCRIPTIVE, sleeps.append) == PRESCRIPTIVE
    assert len(sleeps) == 2
    sleeps.clear()
    assert smoke_until(lambda: CONTROL, PRESCRIPTIVE, sleeps.append) == CONTROL
    assert len(sleeps) == SMOKE_ATTEMPTS - 1


def test_decision_record_names_the_approver_and_failures(tmp_path: Path) -> None:
    plan = plan_ship(analysis_for(results_json(inference=WIN), tmp_path), RUNNING, Path("r.json"))
    outcome = Outcome({"status": "completed", "ended_at": "t"}, "v", None, ["smoke failed"])
    text = decision_record(plan, outcome, "dev", NOW)
    assert text.startswith("## Decision (2026-11-27 06:00 UTC)")
    assert "approved by dev (typed `ship error-style-v1`)" in text
    assert "**Verification failed:**" in text


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
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            sa_database_url=SecretStr(self.database_url),
            sa_server_url="http://server/mcp",
        )
        context = cli.Context(
            load_settings=lambda: settings,
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
    (row,) = workspace.audit_rows()
    assert (row.action, row.experiment_slug, row.approved_by) == ("ship", SLUG, "dev")
    detail = json.loads(row.detail)
    assert detail["decision"] == "ship_variant"
    assert detail["inference"]["Tool calls per session"]["chance_to_win"] == 0.99
    assert detail["served_value"] == ARM_FLAG_VALUES["prescriptive"]
    assert detail["problems"] == []
    report = (workspace.folder / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# Report: error-style-v1\n\n## Decision (2026-11-27 06:00 UTC)")
    assert "Decision: **ship_variant**" in report
    assert "Verified: experiment ended" in report
    assert (workspace.folder / "decision.md").exists()


def test_keep_control_ends_the_experiment_and_leaves_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=LOSS), served=CONTROL)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 0, result.output
    assert workspace.admin.changes == [("control", None)]
    (row,) = workspace.audit_rows()
    assert json.loads(row.detail)["decision"] == "keep_control"


def test_failed_verification_is_recorded_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN), served=CONTROL)
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "verification failed" in result.output
    assert workspace.smokes == SMOKE_ATTEMPTS
    (row,) = workspace.audit_rows()
    assert json.loads(row.detail)["problems"]


def test_ship_refuses_an_ended_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(tmp_path, monkeypatch, results_json(inference=WIN))
    workspace.admin.experiment.update(status="completed", rollout_decision="control")
    result = invoke("ship", SLUG, answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "already ended" in result.output
    assert workspace.unchanged()


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
    assert report.rstrip().endswith(
        "- Verified: experiment ended, flag value and served style match."
    )
