"""`sa start` and `sa doctor` against a fake Flagsmith, a real git repo and a SQLite ledger."""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from typer.testing import CliRunner

from second_audience.config import Settings
from second_audience.core.ledger import Ledger, audit_log
from second_audience.operator import cli
from second_audience.operator.doctor import Report, run_checks
from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError
from second_audience.operator.repo import Repo
from second_audience.operator.spec import load_spec
from second_audience.operator.start import experiment_payload

SPEC_SOURCE = Path(__file__).parents[1] / "experiments" / "error-style-v1.yaml"
ENV = "prodkey"
NOW = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)
SERVER_KEY = "ser.server-key"


class FakeAdmin(FlagsmithAdmin):
    """In-memory Flagsmith: every mutating call is recorded in `changes`."""

    def __init__(self) -> None:
        super().__init__("second-audience", executable="flagsmith")
        self.features: dict[str, dict[str, Any]] = {}
        self.metrics: list[dict[str, Any]] = []
        self.experiments: list[dict[str, Any]] = []
        self.changes: list[str] = []
        self.warehouse = True
        self.logged_in = True

    def whoami(self) -> str:
        if not self.logged_in:
            raise FlagsmithAdminError(["auth", "status"], "Error: not logged in")
        return "dev@example.com"

    def environment_key(self, name: str) -> str:
        return ENV

    def server_keys(self, env: str) -> list[str]:
        return [SERVER_KEY]

    def warehouse_connected(self, env: str) -> bool:
        return self.warehouse

    def get_feature(self, name: str) -> dict[str, Any] | None:
        return self.features.get(name)

    def create_feature(self, name: str, value: str) -> dict[str, Any]:
        if name not in self.features:
            self.changes.append(f"create feature {name}")
            self.features[name] = {
                "id": 100,
                "name": name,
                "default_value": value,
                "variants": [],
            }
        return self.features[name]

    def list_variants(self, name: str) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = self.features[name]["variants"]
        return variants

    def add_variant(self, name: str, key: str, value: str, env: str) -> dict[str, Any]:
        for variant in self.list_variants(name):
            if variant["key"] == key:
                return variant
        self.changes.append(f"add variant {key}")
        variant = {"id": 200, "key": key, "value": value, "weight": 0}
        self.features[name]["variants"].append(variant)
        return variant

    def list_metrics(self, env: str) -> list[dict[str, Any]]:
        return self.metrics

    def create_metric(
        self, env: str, name: str, event: str, aggregation: str, direction: str
    ) -> dict[str, Any]:
        for metric in self.metrics:
            if metric["name"] == name:
                return metric
        self.changes.append(f"create metric {name}")
        metric = {
            "id": 300 + len(self.metrics),
            "name": name,
            "aggregation": aggregation,
            "direction": direction,
            "definition": {"version": 1, "event": event},
        }
        self.metrics.append(metric)
        return metric

    def list_experiments(self, env: str) -> list[dict[str, Any]]:
        return self.experiments

    def get_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
        return next(e for e in self.experiments if e["id"] == experiment_id)

    def create_experiment(self, env: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.changes.append("create experiment")
        # Like the API: the request names the feature by ID, the response nests the object.
        feature = {"id": payload["feature"], "name": "error_style"}
        experiment = {**payload, "feature": feature, "id": 400, "status": "created"}
        experiment["started_at"] = None
        self.experiments.append(experiment)
        return experiment

    def start_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
        self.changes.append("start experiment")
        experiment = self.get_experiment(env, experiment_id)
        experiment.update(status="running", started_at="2026-11-27T06:00:01Z")
        return experiment


def _git(cwd: Path, *args: str) -> None:
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo holding the committed spec and a HYPOTHESIS.md naming it."""
    root = tmp_path / "repo"
    (root / "experiments").mkdir(parents=True)
    shutil.copy(SPEC_SOURCE, root / "experiments" / SPEC_SOURCE.name)
    (root / "HYPOTHESIS.md").write_text("# Hypothesis\n\nExperiment error-style-v1.\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test Approver")
    _git(root, "config", "user.email", "approver@example.com")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "pre-register")
    return root


class Harness:
    """Wires a fake admin, settings and a ledger into the CLI's context."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.spec = root / "experiments" / SPEC_SOURCE.name
        self.admin = FakeAdmin()
        self.database_url = f"sqlite:///{root.parent / 'ledger.db'}"
        self.settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            sa_database_url=SecretStr(self.database_url),
            gemini_api_key=SecretStr("gemini"),
            sa_server_url="http://127.0.0.1:1/mcp",
            flagsmith_environment_key=SecretStr(SERVER_KEY),
        )
        self.server_up = True

        def checks(load: Any, spec_path: Path, make_admin: Any) -> Report:
            return run_checks(
                load, spec_path, make_admin, database=lambda s: "sqlite", server=self._server
            )

        context = cli.Context(
            load_settings=lambda: self.settings,
            make_admin=lambda settings: self.admin,
            checks=checks,
            open_repo=Repo.discover,
            open_ledger=lambda settings: Ledger(create_engine(self.database_url)),
            now=lambda: NOW,
        )
        monkeypatch.setattr(cli, "make_context", lambda: context)

    def _server(self, url: str) -> None:
        if not self.server_up:
            raise ConnectionError(url)

    def invoke(self, *args: str, typed: str | None = None) -> Any:
        """Run `sa` with the given arguments and optional typed confirmation."""
        return CliRunner().invoke(cli.app, list(args), input=typed)

    def state(self) -> Path:
        return self.root / "experiments" / ".state" / "error-style-v1.json"

    def audit_rows(self) -> list[Any]:
        engine = create_engine(self.database_url)
        Ledger(engine)
        with engine.connect() as conn:
            return list(conn.execute(select(audit_log)))


@pytest.fixture
def harness(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(repo, monkeypatch)


def test_dry_run_prints_the_plan_and_changes_nothing(harness: Harness) -> None:
    result = harness.invoke("start", str(harness.spec), "--dry-run")
    assert result.exit_code == 0, result.output
    assert "create flag error_style" in result.output
    assert "create variant prescriptive" in result.output
    assert "create metric 'Tool calls per session': mean of task_finished" in result.output
    assert "START experiment error-style-v1" in result.output
    assert "at least 75 exposed sessions per arm" in result.output
    assert "hard end 2026-11-29T12:00:00+05:30" in result.output
    assert "Dry run: nothing was changed." in result.output
    assert harness.admin.changes == []
    assert not harness.state().exists()


def test_wrong_confirmation_aborts_without_changes(harness: Harness) -> None:
    result = harness.invoke("start", str(harness.spec), typed="error-style-v2\n")
    assert result.exit_code == 1
    assert "confirmation did not match; nothing was changed" in result.output
    assert harness.admin.changes == []
    assert not harness.state().exists()
    assert harness.audit_rows() == []


def test_start_creates_starts_and_records(harness: Harness) -> None:
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 0, result.output
    assert harness.admin.changes == [
        "create feature error_style",
        "add variant prescriptive",
        "create metric Tool calls per session",
        "create metric Task completed correctly",
        "create metric Wrong booking",
        "create experiment",
        "start experiment",
    ]
    assert "Started experiment error-style-v1 (id 400)" in result.output

    state = json.loads(harness.state().read_text(encoding="utf-8"))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=harness.root, capture_output=True, text=True
    ).stdout.strip()
    assert state["experiment_id"] == 400
    assert state["started_at"] == "2026-11-27T06:00:01Z"
    assert state["commit"] == head
    assert state["approved_by"] == "Test Approver"
    assert state["metric_ids"] == {
        "Tool calls per session": 300,
        "Task completed correctly": 301,
        "Wrong booking": 302,
    }
    [row] = harness.audit_rows()
    assert (row.action, row.experiment_slug, row.approved_by) == (
        "start",
        "error-style-v1",
        "Test Approver",
    )
    assert json.loads(row.detail)["experiment_id"] == 400


def test_second_run_reuses_everything_and_reports_running(harness: Harness) -> None:
    assert harness.invoke("start", str(harness.spec), typed="error-style-v1\n").exit_code == 0
    first_changes = list(harness.admin.changes)
    first_state = harness.state().read_text(encoding="utf-8")

    result = harness.invoke("start", str(harness.spec))  # no confirmation needed or given
    assert result.exit_code == 0, result.output
    assert "reuse  flag error_style" in result.output
    assert "nothing to start: experiment error-style-v1 is already running" in result.output
    assert "Experiment error-style-v1 is already running (id 400)." in result.output
    assert harness.admin.changes == first_changes
    assert harness.state().read_text(encoding="utf-8") == first_state
    assert len(harness.audit_rows()) == 1


def test_uncommitted_spec_is_refused(harness: Harness) -> None:
    harness.spec.write_text(
        harness.spec.read_text(encoding="utf-8").replace("75", "60"), encoding="utf-8"
    )
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 1
    assert "commit these first" in result.output
    assert "experiments/error-style-v1.yaml" in result.output
    assert harness.admin.changes == []


def test_untracked_hypothesis_is_refused(harness: Harness) -> None:
    _git(harness.root, "rm", "-q", "--cached", "HYPOTHESIS.md")
    _git(harness.root, "commit", "-q", "-m", "untrack")
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 1
    assert "HYPOTHESIS.md" in result.output
    assert harness.admin.changes == []


def test_hypothesis_must_mention_the_slug(harness: Harness) -> None:
    (harness.root / "HYPOTHESIS.md").write_text("# Hypothesis\n\nSomething else.\n")
    _git(harness.root, "commit", "-q", "-am", "reword")
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 1
    assert "HYPOTHESIS.md does not mention error-style-v1" in result.output
    assert harness.admin.changes == []


def test_conflicting_existing_flag_is_refused(harness: Harness) -> None:
    harness.admin.features["error_style"] = {
        "id": 1,
        "name": "error_style",
        "default_value": '{"style": "temporary"}',
        "variants": [{"id": 2, "key": "spike", "value": "x"}],
    }
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 1
    assert "exists with a different default value" in result.output
    assert "variant 'spike' the spec does not list" in result.output
    assert harness.admin.changes == []


def test_completed_experiment_cannot_be_restarted(harness: Harness) -> None:
    assert harness.invoke("start", str(harness.spec), typed="error-style-v1\n").exit_code == 0
    harness.admin.experiments[0]["status"] = "completed"
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 1
    assert "cannot be restarted" in result.output


def test_start_refuses_when_doctor_fails(harness: Harness) -> None:
    harness.admin.warehouse = False
    result = harness.invoke("start", str(harness.spec), typed="error-style-v1\n")
    assert result.exit_code == 1
    assert "FAIL  warehouse" in result.output
    assert "doctor checks failed; nothing was changed" in result.output
    assert harness.admin.changes == []


def test_doctor_passes_and_lists_every_check(harness: Harness) -> None:
    result = harness.invoke("doctor", str(harness.spec))
    assert result.exit_code == 0, result.output
    for name in (
        "settings",
        "spec",
        "flagsmith cli",
        "flagsmith login",
        "environment",
        "warehouse",
        "server sdk key",
        "database",
        "model key",
        "server",
    ):
        assert f"PASS  {name} " in result.output
    assert "doctor: all checks passed" in result.output


@pytest.mark.parametrize(
    ("breakage", "row"),
    [
        ("logged_out", "FAIL  flagsmith login"),
        ("server_down", "FAIL  server"),
        ("no_model_key", "FAIL  model key"),
        ("wrong_server_key", "FAIL  server sdk key"),
        ("flag_mismatch", "FAIL  spec"),
    ],
)
def test_doctor_exits_non_zero_when_a_check_fails(
    harness: Harness, breakage: str, row: str
) -> None:
    if breakage == "logged_out":
        harness.admin.logged_in = False
    elif breakage == "server_down":
        harness.server_up = False
    elif breakage == "no_model_key":
        harness.settings = harness.settings.model_copy(update={"gemini_api_key": None})
    elif breakage == "wrong_server_key":
        harness.settings = harness.settings.model_copy(
            update={"flagsmith_environment_key": SecretStr("ser.other")}
        )
    else:
        harness.settings = harness.settings.model_copy(update={"sa_flag_name": "other_flag"})
    result = harness.invoke("doctor", str(harness.spec))
    assert result.exit_code == 1
    assert row in result.output
    assert "doctor: at least one check failed" in result.output


def test_experiment_payload_matches_the_spec() -> None:
    spec = load_spec(SPEC_SOURCE)
    metric_ids = {"Tool calls per session": 1, "Task completed correctly": 2, "Wrong booking": 3}
    payload = experiment_payload(spec, 10, {"prescriptive": 20}, metric_ids)
    assert payload["feature"] == 10
    assert payload["name"] == "error-style-v1"
    assert payload["metrics"] == [
        {"metric": 1, "expected_direction": "decrease"},
        {"metric": 2, "expected_direction": "not_decrease"},
        {"metric": 3, "expected_direction": "not_increase"},
    ]
    rollout = payload["experiment_rollout"]
    assert rollout["rollout_percentage"] == 100
    assert rollout["feature_state_value"] == {
        "type": "string",
        "value": spec.control.flag_value,
    }
    assert rollout["multivariate_feature_state_values"] == [
        {"multivariate_feature_option": 20, "percentage_allocation": 50}
    ]
