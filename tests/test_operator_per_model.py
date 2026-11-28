"""`sa ship --per-model`: the per-model rule, the plan, and the CLI with Flagsmith faked."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from second_audience.core.ledger import SessionRecord
from second_audience.core.styles import ARM_FLAG_VALUES, CONTROL, PRESCRIPTIVE, ErrorStyle
from second_audience.operator.analysis import SMOKE_AGENT_LABEL, Analysis, Cell
from second_audience.operator.flagsmith_admin import FlagsmithAdminError
from second_audience.operator.per_model import (
    EXPLORATORY,
    MIN_PER_ARM,
    PER_MODEL_FILE,
    judge,
    labels_in,
    model_rows,
    plan_per_model,
    rules_for,
)
from second_audience.operator.ship import ShipRefusedError
from second_audience.operator.stats import wilson
from tests.test_operator_ship import (
    SLUG,
    VARIANT_ID,
    FakeAdmin,
    Workspace,
    _feature_state,
    analysis_for,
    invoke,
    results_json,
)

SEGMENT = f"sa-{SLUG}-rollout"
SEGMENT_ID = 77
COMPLETED = {"id": 680, "status": "completed", "rollout_decision": "control"}
AT = datetime(2026, 11, 26, 16, 0, tzinfo=UTC)


def cell(arm: str, n: int, correct: int, wrong: int) -> Cell:
    return Cell("m", arm, n, wilson(correct, n), wilson(wrong, n), 4.0 if n else None)


# --- the rule ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("control", "variant", "included", "reason"),
    [
        ((20, 10, 2), (20, 15, 2), True, "meets the rule"),
        ((20, 10, 2), (20, 15, 1), True, "meets the rule"),
        ((19, 10, 2), (40, 30, 0), False, f"fewer than {MIN_PER_ARM} scored sessions in an arm"),
        ((40, 10, 2), (19, 15, 0), False, f"fewer than {MIN_PER_ARM} scored sessions in an arm"),
        ((20, 15, 2), (20, 15, 0), False, "correct rate not higher"),
        ((20, 15, 2), (20, 10, 0), False, "correct rate not higher"),
        ((20, 10, 2), (20, 15, 3), False, "wrong-booking rate higher"),
        ((0, 0, 0), (25, 25, 0), False, f"fewer than {MIN_PER_ARM} scored sessions in an arm"),
    ],
)
def test_the_per_model_rule(
    control: tuple[int, int, int], variant: tuple[int, int, int], included: bool, reason: str
) -> None:
    row = judge("m", cell("control", *control), cell("prescriptive", *variant))
    assert (row.included, row.reason) == (included, reason)


def test_rates_not_counts_decide() -> None:
    # 20 of 40 is a lower rate than 12 of 20, although it is more sessions.
    row = judge("m", cell("control", 20, 12, 1), cell("prescriptive", 40, 20, 1))
    assert row.reason == "correct rate not higher"


def test_a_label_with_a_comma_is_never_included() -> None:
    row = judge("a,b", cell("control", 30, 10, 2), cell("prescriptive", 30, 25, 0))
    assert (row.included, row.reason) == (False, "the label contains a comma")


def test_rules_round_trip_in_both_value_shapes() -> None:
    rules = rules_for(["b", "a"])
    assert rules["rules"][0]["conditions"][0]["value"] == "a,b"
    assert labels_in(rules) == {"a", "b"}
    as_stored = {"type": "ALL", "rules": [{"type": "ANY", "conditions": [
        {"property": "agent_label", "operator": "IN", "value": ["a", "b"]}
    ]}]}  # fmt: skip
    assert labels_in(as_stored) == {"a", "b"}
    other = {"rules": [{"conditions": [{"property": "source", "operator": "IN", "value": "a"}]}]}
    assert labels_in(other) is None
    assert labels_in(None) is None


# --- fixture breakdowns -----------------------------------------------------------------------


def session(model: str, variant: str, final_state: str, n: int) -> list[SessionRecord]:
    return [
        SessionRecord(
            session_id=f"{model}-{variant}-{final_state}-{i}",
            scenario_id="s01",
            scenario_kind="aligned",
            source="lab",
            agent_label=model,
            variant=variant,
            in_experiment=True,
            started_at=AT,
            exposed_at=AT,
            finished_at=AT,
            outcome=final_state,
            final_state=final_state,
            tool_call_count=4,
        )
        for i in range(n)
    ]


# helped: 50% -> 90% correct, wrong 2 -> 0. harmed: wrong rises. small: under 20 per arm.
RECORDS = [
    *session("helped", "control", "correct", 10),
    *session("helped", "control", "no_booking", 8),
    *session("helped", "control", "incorrect", 2),
    *session("helped", "prescriptive", "correct", 18),
    *session("helped", "prescriptive", "no_booking", 2),
    *session("harmed", "control", "correct", 20),
    *session("harmed", "prescriptive", "correct", 15),
    *session("harmed", "prescriptive", "incorrect", 5),
    *session("small", "control", "no_booking", 5),
    *session("small", "prescriptive", "correct", 5),
    *session(SMOKE_AGENT_LABEL, "prescriptive", "correct", 30),  # never part of the analysis
]


def with_records(analysis: Analysis, records: list[SessionRecord]) -> Analysis:
    return replace(analysis, records=[r for r in records if r.agent_label != SMOKE_AGENT_LABEL])


def test_model_rows_over_a_fixture_breakdown(tmp_path: Path) -> None:
    analysis = with_records(analysis_for(results_json(), tmp_path), RECORDS)
    rows = {row.label: row for row in model_rows(analysis)}
    assert set(rows) == {"helped", "harmed", "small"}
    assert rows["helped"].included
    assert (rows["helped"].control.n, rows["helped"].variant.n) == (20, 20)
    assert rows["harmed"].reason == "correct rate not higher"
    assert rows["small"].reason == f"fewer than {MIN_PER_ARM} scored sessions in an arm"


CONTROL_STATE = _feature_state(ARM_FLAG_VALUES["control"], 0.0)


def test_plan_needs_an_ended_experiment(tmp_path: Path) -> None:
    analysis = with_records(analysis_for(results_json(), tmp_path), RECORDS)
    with pytest.raises(ShipRefusedError, match="locked while it runs"):
        plan_per_model(analysis, {"status": "running"}, CONTROL_STATE, None, Path("r"))


def test_plan_needs_control_served_to_everyone(tmp_path: Path) -> None:
    analysis = with_records(analysis_for(results_json(), tmp_path), RECORDS)
    shipped = _feature_state(ARM_FLAG_VALUES["control"], 100.0)
    with pytest.raises(ShipRefusedError, match="not control to everyone"):
        plan_per_model(analysis, COMPLETED, shipped, None, Path("r"))
    disabled = _feature_state(ARM_FLAG_VALUES["control"], 0.0, enabled=False)
    with pytest.raises(ShipRefusedError, match="not control to everyone"):
        plan_per_model(analysis, COMPLETED, disabled, None, Path("r"))


def test_plan_reuses_a_matching_segment_and_refuses_another(tmp_path: Path) -> None:
    analysis = with_records(analysis_for(results_json(), tmp_path), RECORDS)
    same = {"id": SEGMENT_ID, "name": SEGMENT, "rules": rules_for(["helped"])}
    plan = plan_per_model(analysis, COMPLETED, CONTROL_STATE, same, Path("r"))
    assert plan.existing_segment_id == SEGMENT_ID
    assert "reuse segment" in plan.changes()[0]
    other = {"id": SEGMENT_ID, "name": SEGMENT, "rules": rules_for(["harmed"])}
    with pytest.raises(ShipRefusedError, match="already exists"):
        plan_per_model(analysis, COMPLETED, CONTROL_STATE, other, Path("r"))


def test_smoke_labels_cover_each_included_model_and_one_excluded(tmp_path: Path) -> None:
    analysis = with_records(analysis_for(results_json(), tmp_path), RECORDS)
    plan = plan_per_model(analysis, COMPLETED, CONTROL_STATE, None, Path("r"))
    assert plan.included == ["helped"]
    assert plan.smoke_labels() == [("helped", PRESCRIPTIVE), ("harmed", CONTROL)]
    only = with_records(analysis, [r for r in RECORDS if r.agent_label == "helped"])
    plan = plan_per_model(only, COMPLETED, CONTROL_STATE, None, Path("r"))
    assert plan.smoke_labels()[-1] == (SMOKE_AGENT_LABEL, CONTROL)


# --- the CLI ----------------------------------------------------------------------------------


class SegmentAdmin(FakeAdmin):
    """`FakeAdmin` plus segments; the override acts on `served_for`, like Flagsmith would."""

    def __init__(self, raw: dict[str, Any]) -> None:
        super().__init__(raw, status="completed")
        self.experiment.update(rollout_decision="control", ended_at="2026-11-28T10:00:00Z")
        self.segment: dict[str, Any] | None = None
        self.override: dict[str, Any] | None = None
        self.segment_changes: list[str] = []
        self.fail_override = False

    def get_segment(self, name: str) -> dict[str, Any] | None:
        return dict(self.segment) if self.segment and self.segment["name"] == name else None

    def create_segment(self, name: str, rules: dict[str, Any], description: str) -> dict[str, Any]:
        assert EXPLORATORY in description
        self.segment_changes.append(f"create {name}")
        self.segment = {"id": SEGMENT_ID, "name": name, "rules": rules}
        return dict(self.segment)

    def set_segment_override(
        self, feature: str, segment_id: int, value: str, variant_keys: list[str], env: str
    ) -> None:
        self.segment_changes.append(f"override {feature} {segment_id}")
        if self.fail_override:
            raise FlagsmithAdminError(["flag", "update"], "Error: boom")
        assert variant_keys == ["prescriptive"]
        variants = [{"id": VARIANT_ID, "key": "prescriptive", "weight": 0}]
        self.override = {"enabled": True, "value": value, "variants": variants}

    def segment_override(self, feature: str, segment_id: int, env: str) -> dict[str, Any]:
        assert self.override is not None
        return self.override

    def served_for(self, label: str) -> ErrorStyle:
        labels = labels_in(self.segment["rules"]) if self.segment else set()
        if self.override and labels and label in labels:
            return ErrorStyle.from_flag_value(self.override["value"])
        return CONTROL


class PerModelWorkspace(Workspace):
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        raw = results_json()
        super().__init__(root, monkeypatch, raw)
        self.admin = SegmentAdmin(raw)
        monkeypatch.setattr(self.ledger, "sessions_since", lambda since: list(RECORDS))

    def _smoke(self, settings: Any, label: str) -> ErrorStyle:
        self.smokes += 1
        self.labels.append(label)
        assert isinstance(self.admin, SegmentAdmin)
        return self.admin.served_for(label)


def test_per_model_dry_run_prints_the_table_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = PerModelWorkspace(tmp_path, monkeypatch)
    result = invoke("ship", SLUG, "--per-model", "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"({EXPLORATORY})" in result.output
    assert "Rule: include a model when it has at least 20 scored sessions" in result.output
    assert "| helped | 20 | 20 | 50.0% (10/20) | 90.0% (18/20) |" in result.output
    assert "control (correct rate not higher)" in result.output
    assert f"create project segment {SEGMENT}: agent_label in ['helped']" in result.output
    assert "Dry run: nothing was changed." in result.output
    assert isinstance(workspace.admin, SegmentAdmin)
    assert workspace.admin.segment_changes == []
    assert workspace.unchanged()


def test_per_model_wrong_confirmation_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = PerModelWorkspace(tmp_path, monkeypatch)
    result = invoke("ship", SLUG, "--per-model", answer=f"ship {SLUG}\n")
    assert result.exit_code == 1
    assert "confirmation did not match; nothing was changed" in result.output
    assert isinstance(workspace.admin, SegmentAdmin)
    assert workspace.admin.segment_changes == []
    assert workspace.unchanged()


def test_per_model_refuses_while_the_experiment_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = PerModelWorkspace(tmp_path, monkeypatch)
    workspace.admin.experiment["status"] = "running"
    result = invoke("ship", SLUG, "--per-model", "--dry-run")
    assert result.exit_code == 1
    assert "refused" in result.output
    assert "end it first" in result.output
    assert workspace.unchanged()


def test_per_model_applies_verifies_smokes_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = PerModelWorkspace(tmp_path, monkeypatch)
    result = invoke("ship", SLUG, "--per-model", answer=f"ship {SLUG} per-model\n")
    assert result.exit_code == 0, result.output
    admin = workspace.admin
    assert isinstance(admin, SegmentAdmin)
    assert admin.segment_changes == [f"create {SEGMENT}", f"override error_style {SEGMENT_ID}"]
    assert admin.changes == []  # complete/ is never called
    assert workspace.labels == ["helped", "harmed"]
    (row,) = workspace.audit_rows()
    assert (row.action, row.experiment_slug, row.approved_by) == ("ship_per_model", SLUG, "dev")
    detail = json.loads(row.detail)
    assert detail["included"] == ["helped"]
    assert detail["exploratory"] == EXPLORATORY
    assert detail["models"]["helped"]["prescriptive"] == {"n": 20, "correct": 18, "wrong": 0}
    assert detail["smoke"]["harmed"] == CONTROL.model_dump()
    assert detail["problems"] == []
    report = (workspace.folder / "report.md").read_text(encoding="utf-8")
    assert f"## Per-model rollout (2026-11-27 06:00 UTC), {EXPLORATORY}" in report
    assert "**Exploratory:**" in report
    assert "Verified: segment and override read back as planned" in report
    assert (workspace.folder / PER_MODEL_FILE).read_text(encoding="utf-8") in report


def test_per_model_records_a_failed_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = PerModelWorkspace(tmp_path, monkeypatch)
    assert isinstance(workspace.admin, SegmentAdmin)
    workspace.admin.fail_override = True
    result = invoke("ship", SLUG, "--per-model", answer=f"ship {SLUG} per-model\n")
    assert result.exit_code == 1
    assert "applying the rollout failed" in result.output
    assert workspace.smokes == 0
    (row,) = workspace.audit_rows()
    assert json.loads(row.detail)["problems"][0].startswith("applying the rollout failed")
    assert "**Verification failed:**" in (workspace.folder / PER_MODEL_FILE).read_text("utf-8")


def test_per_model_with_no_qualifying_model_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = PerModelWorkspace(tmp_path, monkeypatch)
    harmed = [r for r in RECORDS if r.agent_label == "harmed"]
    monkeypatch.setattr(workspace.ledger, "sessions_since", lambda since: harmed)
    result = invoke("ship", SLUG, "--per-model", answer=f"ship {SLUG} per-model\n")
    assert result.exit_code == 0, result.output
    assert "No model meets the rule" in result.output
    assert workspace.unchanged()
