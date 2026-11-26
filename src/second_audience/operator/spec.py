"""Experiment spec: the reviewed YAML file that `sa start` turns into a running experiment.

The file is the human-approved source of truth. Validation catches every mistake that would
otherwise surface as a half-created experiment in Flagsmith.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from second_audience.core.experiment import EVENTS
from second_audience.core.styles import ErrorStyle

CONTROL_KEY = "control"

Direction = Literal["up", "down"]
Aggregation = Literal["occurrence", "mean", "sum", "count"]
MetricRole = Literal["primary", "secondary", "guardrail"]

# Flagsmith's `expected_direction` per role and direction. A guardrail only has to avoid
# getting worse, so it maps to the "not_" forms (docs/spike-findings.md, question 6).
_EXPECTED_DIRECTION: dict[tuple[MetricRole, Direction], str] = {
    ("primary", "up"): "increase",
    ("primary", "down"): "decrease",
    ("secondary", "up"): "increase",
    ("secondary", "down"): "decrease",
    ("guardrail", "up"): "not_decrease",
    ("guardrail", "down"): "not_increase",
}


class _Strict(BaseModel):
    """Base for spec models: unknown keys are typos, so they are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Arm(_Strict):
    """One arm: its variant key and the error style its flag value encodes."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: dict[str, StrictBool]

    @field_validator("value")
    @classmethod
    def _is_error_style(cls, value: dict[str, bool]) -> dict[str, bool]:
        """Every key must be an `ErrorStyle` field, so a typo cannot silently mean false."""
        unknown = set(value) - set(ErrorStyle.model_fields)
        if unknown:
            raise ValueError(f"unknown error style keys: {sorted(unknown)}")
        return value

    @property
    def style(self) -> ErrorStyle:
        """The parsed error style."""
        return ErrorStyle.model_validate(self.value)

    @property
    def flag_value(self) -> str:
        """The JSON string stored on the Flagsmith flag, in canonical field order."""
        return json.dumps(self.style.model_dump())


class Metric(_Strict):
    """A Flagsmith metric over one of the events the server sends."""

    name: str = Field(min_length=1)
    event: str
    direction: Direction
    aggregation: Aggregation = "occurrence"
    role: MetricRole = "secondary"

    @field_validator("event")
    @classmethod
    def _known_event(cls, event: str) -> str:
        """Only events the server actually sends can ever produce data."""
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}; the server sends {sorted(EVENTS)}")
        return event

    @property
    def expected_direction(self) -> str:
        """Flagsmith's `expected_direction` for this metric in the experiment."""
        return _EXPECTED_DIRECTION[(self.role, self.direction)]


class Allocation(_Strict):
    """Share of identities enrolled, and how enrolled identities split across arms."""

    traffic_percent: float = Field(gt=0, le=100)
    split: dict[str, float]

    @field_validator("split")
    @classmethod
    def _sums_to_100(cls, split: dict[str, float]) -> dict[str, float]:
        """Weights are percentages of enrolled identities."""
        if any(weight < 0 for weight in split.values()):
            raise ValueError("split weights must not be negative")
        if abs(sum(split.values()) - 100) > 1e-9:
            raise ValueError(f"split must sum to 100, got {sum(split.values())}")
        return split


class StoppingRule(_Strict):
    """When the experiment may be read, and the last moment it runs."""

    min_exposed_per_arm: int = Field(gt=0)
    win_probability_threshold: float = Field(gt=0.5, lt=1)
    hard_end: AwareDatetime


class ExperimentSpec(_Strict):
    """A complete, validated experiment definition."""

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    environment: str = Field(min_length=1)
    flag: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    control: Arm
    variants: list[Arm] = Field(min_length=1)
    metrics: list[Metric] = Field(min_length=1)
    allocation: Allocation
    stopping_rule: StoppingRule

    @field_validator("hypothesis")
    @classmethod
    def _one_line(cls, hypothesis: str) -> str:
        """YAML folded scalars keep a trailing newline; Flagsmith shows the text as is."""
        return " ".join(hypothesis.split())

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        """Cross-field rules: arm keys, split keys, a single primary metric."""
        if self.control.key != CONTROL_KEY:
            raise ValueError(f"the control arm's key must be {CONTROL_KEY!r}")
        keys = [arm.key for arm in self.arms]
        if len(set(keys)) != len(keys):
            raise ValueError(f"arm keys must be unique, got {keys}")
        if set(self.allocation.split) != set(keys):
            raise ValueError(
                f"split keys {sorted(self.allocation.split)} must match the arms {sorted(keys)}"
            )
        styles = [arm.flag_value for arm in self.arms]
        if len(set(styles)) != len(styles):
            raise ValueError("every arm must have a different error style")
        primaries = [metric.name for metric in self.metrics if metric.role == "primary"]
        if len(primaries) != 1:
            raise ValueError(f"exactly one metric must be primary, got {len(primaries)}")
        names = [metric.name for metric in self.metrics]
        if len(set(names)) != len(names):
            raise ValueError(f"metric names must be unique, got {names}")
        return self

    @property
    def arms(self) -> list[Arm]:
        """Control first, then the variants."""
        return [self.control, *self.variants]

    @property
    def primary_metric(self) -> Metric:
        """The one metric the decision rule reads."""
        return next(metric for metric in self.metrics if metric.role == "primary")

    def hard_end_passed(self, now: datetime) -> bool:
        """Whether the hard end is already in the past."""
        return now >= self.stopping_rule.hard_end


def load_spec(path: Path) -> ExperimentSpec:
    """Read and validate a spec file; raises `ValueError` with the reason on bad input."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return ExperimentSpec.model_validate(raw)
