"""`sa start`: plan, confirm, and create-or-reuse everything an experiment needs, then start it.

Planning only reads Flagsmith. Nothing changes until the human types the spec's slug.
Every object is looked up before it is created, so a second run reuses what the first made
and reports that the experiment is already running.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from second_audience.operator.flagsmith_admin import FlagsmithAdmin, feature_id_of
from second_audience.operator.spec import ExperimentSpec

RUNNING = "running"
CREATED = "created"
_ACTIVE = frozenset({CREATED, RUNNING, "paused"})


class StartRefusedError(RuntimeError):
    """`sa start` will not proceed; the message says why and changes nothing."""


@dataclass
class Plan:
    """What exists in Flagsmith for this spec, and what `apply` would do about it."""

    spec: ExperimentSpec
    environment_key: str
    feature: dict[str, Any] | None
    variants: dict[str, dict[str, Any] | None]
    metrics: dict[str, dict[str, Any] | None]
    experiment: dict[str, Any] | None
    problems: list[str] = field(default_factory=list)

    @property
    def already_running(self) -> bool:
        """The spec's experiment exists and is running: nothing to do."""
        return self.experiment is not None and self.experiment.get("status") == RUNNING

    def steps(self) -> list[str]:
        """Human-readable actions, in the order `apply` performs them."""
        spec = self.spec
        steps = [
            _step(self.feature, f"flag {spec.flag} (default = {spec.control.key} arm)"),
            *(
                _step(self.variants[arm.key], f"variant {arm.key} = {arm.flag_value}")
                for arm in spec.variants
            ),
            *(
                _step(
                    self.metrics[metric.name],
                    f"metric {metric.name!r}: {metric.aggregation} of {metric.event}, "
                    f"{metric.role}, expected {metric.expected_direction}",
                )
                for metric in spec.metrics
            ),
            _step(self.experiment, f"experiment {spec.slug}"),
        ]
        if self.already_running:
            steps.append(f"nothing to start: experiment {spec.slug} is already running")
        else:
            steps.append(f"START experiment {spec.slug} (locks the flag; cannot be undone)")
        return steps


def _step(existing: dict[str, Any] | None, what: str) -> str:
    """One plan line: reuse what exists, create what does not."""
    return f"reuse  {what}" if existing is not None else f"create {what}"


def make_plan(spec: ExperimentSpec, admin: FlagsmithAdmin, environment_key: str) -> Plan:
    """Read the current Flagsmith state for the spec and note anything that conflicts."""
    feature = admin.get_feature(spec.flag)
    existing_variants = {v.get("key"): v for v in (feature or {}).get("variants", [])}
    existing_metrics = {m.get("name"): m for m in admin.list_metrics(environment_key)}
    experiments = admin.list_experiments(environment_key)
    feature_id = feature["id"] if feature else None
    on_flag = [e for e in experiments if feature_id is not None and feature_id_of(e) == feature_id]
    mine = next((e for e in on_flag if e.get("name") == spec.slug), None)

    plan = Plan(
        spec=spec,
        environment_key=environment_key,
        feature=feature,
        variants={arm.key: existing_variants.get(arm.key) for arm in spec.variants},
        metrics={metric.name: existing_metrics.get(metric.name) for metric in spec.metrics},
        experiment=mine,
    )
    _find_conflicts(plan, existing_variants, on_flag)
    return plan


def _find_conflicts(
    plan: Plan, existing_variants: dict[Any, dict[str, Any]], on_flag: list[dict[str, Any]]
) -> None:
    """Existing objects that differ from the spec would make the run measure something else."""
    spec = plan.spec
    if plan.feature is not None and plan.feature.get("default_value") != spec.control.flag_value:
        plan.problems.append(
            f"flag {spec.flag} exists with a different default value "
            f"({plan.feature.get('default_value')!r}); remove it or fix the spec"
        )
    expected = {arm.key for arm in spec.variants}
    for key in existing_variants:
        if key not in expected:
            plan.problems.append(f"flag {spec.flag} has a variant {key!r} the spec does not list")
    for arm in spec.variants:
        existing_variant = plan.variants[arm.key]
        if existing_variant is not None and existing_variant.get("value") != arm.flag_value:
            plan.problems.append(f"variant {arm.key} exists with a different value")
    for metric in spec.metrics:
        existing = plan.metrics[metric.name]
        if existing is None:
            continue
        found = (
            existing.get("definition", {}).get("event"),
            existing.get("aggregation"),
            existing.get("direction"),
        )
        if found != (metric.event, metric.aggregation, metric.direction):
            plan.problems.append(f"metric {metric.name!r} exists with a different definition")
    if plan.experiment is not None and plan.experiment.get("status") not in _ACTIVE:
        plan.problems.append(
            f"experiment {spec.slug} is {plan.experiment.get('status')}; an ended experiment "
            "cannot be restarted, so use a new slug"
        )
    for other in on_flag:
        if other is not plan.experiment and other.get("status") in _ACTIVE:
            plan.problems.append(
                f"flag {spec.flag} already has an active experiment {other.get('name')!r}"
            )


def experiment_payload(
    spec: ExperimentSpec, feature_id: int, variant_ids: dict[str, int], metric_ids: dict[str, int]
) -> dict[str, Any]:
    """The `POST .../experiments/` body (Flagsmith's coding-agent guide; spike question 6)."""
    split = spec.allocation.split
    return {
        "feature": feature_id,
        "name": spec.slug,
        "hypothesis": spec.hypothesis,
        "metrics": [
            {"metric": metric_ids[metric.name], "expected_direction": metric.expected_direction}
            for metric in spec.metrics
        ],
        "experiment_rollout": {
            "enabled": True,
            "rollout_percentage": spec.allocation.traffic_percent,
            "feature_state_value": {"type": "string", "value": spec.control.flag_value},
            # Control takes whatever the variants do not.
            "multivariate_feature_state_values": [
                {
                    "multivariate_feature_option": variant_ids[arm.key],
                    "percentage_allocation": split[arm.key],
                }
                for arm in spec.variants
            ],
        },
    }


@dataclass(frozen=True)
class Started:
    """IDs of everything the experiment uses, for the state file and the audit log."""

    feature_id: int
    variant_ids: dict[str, int]
    metric_ids: dict[str, int]
    experiment: dict[str, Any]


def apply_plan(plan: Plan, admin: FlagsmithAdmin) -> Started:
    """Create what is missing, start the experiment if needed, and verify it is running."""
    if plan.problems:
        raise StartRefusedError("; ".join(plan.problems))
    spec, env = plan.spec, plan.environment_key
    feature = admin.create_feature(spec.flag, spec.control.flag_value)
    variant_ids = {
        arm.key: int(admin.add_variant(spec.flag, arm.key, arm.flag_value, env)["id"])
        for arm in spec.variants
    }
    metric_ids = {
        metric.name: int(
            admin.create_metric(
                env, metric.name, metric.event, metric.aggregation, metric.direction
            )["id"]
        )
        for metric in spec.metrics
    }
    experiment = plan.experiment or admin.create_experiment(
        env, experiment_payload(spec, int(feature["id"]), variant_ids, metric_ids)
    )
    if experiment.get("status") != RUNNING:
        admin.start_experiment(env, int(experiment["id"]))
    experiment = admin.get_experiment(env, int(experiment["id"]))
    if experiment.get("status") != RUNNING:
        raise StartRefusedError(
            f"experiment {spec.slug} is {experiment.get('status')!r} after start, not running"
        )
    return Started(int(feature["id"]), variant_ids, metric_ids, experiment)


def state_path(spec_path: Path, slug: str) -> Path:
    """`experiments/.state/<slug>.json`, next to the spec."""
    return spec_path.parent / ".state" / f"{slug}.json"


def write_state(
    path: Path,
    plan: Plan,
    started: Started,
    commit: str,
    approved_by: str,
    now: Callable[[], datetime],
) -> dict[str, Any]:
    """Record the IDs, start time and commit; keep the first record if one exists."""
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return existing
    state = {
        "slug": plan.spec.slug,
        "environment": plan.spec.environment,
        "environment_key": plan.environment_key,
        "flag": plan.spec.flag,
        "feature_id": started.feature_id,
        "variant_ids": started.variant_ids,
        "metric_ids": started.metric_ids,
        "experiment_id": started.experiment["id"],
        "started_at": started.experiment.get("started_at"),
        "commit": commit,
        "approved_by": approved_by,
        "recorded_at": now().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state
