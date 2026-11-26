"""Experiment spec validation."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from second_audience.core.styles import ARM_FLAG_VALUES
from second_audience.operator.spec import ExperimentSpec, Metric, load_spec

SPEC_PATH = Path(__file__).parents[1] / "experiments" / "error-style-v1.yaml"


def _raw() -> dict[str, Any]:
    """The committed spec as a plain dict, to mutate in tests."""
    raw: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    return raw


def test_the_committed_spec_is_valid() -> None:
    spec = load_spec(SPEC_PATH)
    assert spec.slug == "error-style-v1"
    assert [arm.key for arm in spec.arms] == ["control", "prescriptive"]
    assert spec.primary_metric.event == "task_finished"
    assert spec.hypothesis.count("\n") == 0


def test_arm_flag_values_match_what_the_server_parses() -> None:
    spec = load_spec(SPEC_PATH)
    assert {arm.key: arm.flag_value for arm in spec.arms} == ARM_FLAG_VALUES


@pytest.mark.parametrize(
    ("role", "direction", "expected"),
    [
        ("primary", "up", "increase"),
        ("secondary", "down", "decrease"),
        ("guardrail", "down", "not_increase"),
        ("guardrail", "up", "not_decrease"),
    ],
)
def test_expected_direction_follows_role_and_direction(
    role: str, direction: str, expected: str
) -> None:
    metric = Metric.model_validate(
        {"name": "m", "event": "task_completed", "direction": direction, "role": role}
    )
    assert metric.expected_direction == expected


def _mutate(path: str, value: Any) -> dict[str, Any]:
    """The committed spec with one dotted key replaced."""
    raw = _raw()
    *parents, last = path.split(".")
    node = raw
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return raw


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("allocation.split", {"control": 50, "prescriptive": 40}, "sum to 100"),
        ("allocation.split", {"control": 50, "other": 50}, "must match the arms"),
        ("metrics.1.role", "primary", "exactly one metric must be primary"),
        ("metrics.0.role", "secondary", "exactly one metric must be primary"),
        ("metrics.0.event", "task_done", "unknown event"),
        ("variants.0.value", {"suggest_alternative": True, "corected_call": True}, "unknown"),
        ("variants.0.value", {"suggest_alternative": "yes"}, "valid boolean"),
        ("control.key", "baseline", "control arm's key"),
        ("variants.0.key", "control", "unique"),
        ("variants.0.value", {}, "different error style"),
        ("stopping_rule.hard_end", "2026-11-29T12:00:00", "timezone"),
        ("stopping_rule.win_probability_threshold", 1.2, "less than 1"),
        ("slug", "Error Style", "pattern"),
        ("surprise", 1, "Extra inputs"),
    ],
)
def test_invalid_specs_are_rejected(path: str, value: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ExperimentSpec.model_validate(_mutate(path, value))


def test_load_spec_rejects_non_mapping_and_bad_yaml(tmp_path: Path) -> None:
    listed = tmp_path / "list.yaml"
    listed.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_spec(listed)
    broken = tmp_path / "broken.yaml"
    broken.write_text("slug: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_spec(broken)
