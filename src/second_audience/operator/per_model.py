"""`sa ship --per-model`: roll the variant out to only the agent models it helped (SA-21).

After an experiment has ended with control served to everyone, a Flagsmith segment on the
`agent_label` identity trait and a segment override on the flag give the variant's value to
the chosen models only. The server needs no change: it already sends `agent_label` on the
call that resolves the flag (amendment A1), and Flagsmith evaluates the segment against the
traits of that same call (verified in Development, `docs/spike-findings.md`, SA-21).

Per-model results are exploratory. The rule below is shown to the human, applied only on a
typed confirmation, and every output says it rests on small per-model samples.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from second_audience.core.styles import ErrorStyle
from second_audience.operator.analysis import SMOKE_AGENT_LABEL, Analysis, Cell, breakdown
from second_audience.operator.flagsmith_admin import FlagsmithAdmin
from second_audience.operator.ship import COMPLETED, ShipRefusedError, served_value
from second_audience.operator.stats import Interval

MIN_PER_ARM = 20
"""A model is considered only with at least this many scored sessions in each arm."""
EXPLORATORY = "exploratory, based on small per-model samples"
PER_MODEL_FILE = "per-model.md"
"""The per-model records, kept beside `decision.md` and added to the report after it."""
TRAIT = "agent_label"
RULE = (
    f"include a model when it has at least {MIN_PER_ARM} scored sessions in each arm, its "
    "correct rate is higher under the variant, and its wrong-booking rate is not higher; "
    "every other model keeps the control value"
)


def segment_name(slug: str) -> str:
    """The rollout segment's name for an experiment."""
    return f"sa-{slug}-rollout"


def confirmation(slug: str) -> str:
    """The phrase the human types to apply a per-model rollout."""
    return f"ship {slug} per-model"


@dataclass(frozen=True)
class ModelRow:
    """One model's scored sessions in each arm, and whether the rule includes it."""

    label: str
    control: Cell
    variant: Cell
    included: bool
    reason: str


def _rate(interval: Interval | None) -> float | None:
    """The observed proportion, or `None` without sessions."""
    return interval.rate if interval is not None else None


def judge(label: str, control: Cell, variant: Cell) -> ModelRow:
    """Apply the per-model rule to one model's two cells."""
    if "," in label:
        # A comma would split the label in the segment's IN condition.
        return ModelRow(label, control, variant, False, "the label contains a comma")
    if min(control.n, variant.n) < MIN_PER_ARM:
        reason = f"fewer than {MIN_PER_ARM} scored sessions in an arm"
        return ModelRow(label, control, variant, False, reason)
    correct = (_rate(control.correct), _rate(variant.correct))
    wrong = (_rate(control.wrong), _rate(variant.wrong))
    # Both arms have at least MIN_PER_ARM sessions here, so every rate is present.
    if not (correct[0] is not None and correct[1] is not None and correct[1] > correct[0]):
        return ModelRow(label, control, variant, False, "correct rate not higher")
    if not (wrong[0] is not None and wrong[1] is not None and wrong[1] <= wrong[0]):
        return ModelRow(label, control, variant, False, "wrong-booking rate higher")
    return ModelRow(label, control, variant, True, "meets the rule")


def model_rows(analysis: Analysis) -> list[ModelRow]:
    """Every model with scored sessions, judged by the rule; smoke sessions are left out."""
    control, variant = analysis.spec.control.key, analysis.spec.variants[0].key
    cells = breakdown(analysis.spec, analysis.records, lambda r: r.agent_label)
    by_group: dict[str, dict[str, Cell]] = {}
    for cell in cells:
        by_group.setdefault(cell.group, {})[cell.arm] = cell
    return [judge(label, arms[control], arms[variant]) for label, arms in by_group.items()]


def rules_for(labels: Iterable[str]) -> dict[str, Any]:
    """The segment's rule tree: `agent_label` is one of `labels`."""
    condition = {"property": TRAIT, "operator": "IN", "value": ",".join(sorted(labels))}
    return {"type": "ALL", "rules": [{"type": "ANY", "conditions": [condition]}]}


def labels_in(rules: Any) -> set[str] | None:
    """The labels a rule tree matches, or `None` if it is anything but one `IN` on the trait.

    Flagsmith stores an `IN` value as a list; a comma-separated string is also read.
    """
    conditions: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            conditions.extend(node.get("conditions") or [])
            walk(node.get("rules") or [])

    walk(rules)
    if len(conditions) != 1:
        return None
    only = conditions[0]
    if only.get("property") != TRAIT or only.get("operator") != "IN":
        return None
    value = only.get("value")
    if isinstance(value, str):
        return {part for part in value.split(",") if part}
    if isinstance(value, list):
        return {str(part) for part in value}
    return None


@dataclass(frozen=True)
class PerModelPlan:
    """The table, the chosen models, and the segment and override that carry them out."""

    analysis: Analysis
    results_file: Path
    rows: list[ModelRow]
    existing_segment_id: int | None = None
    """A segment of this name already matches the chosen models; it is reused, not created."""

    @property
    def slug(self) -> str:
        """The experiment's slug."""
        return self.analysis.spec.slug

    @property
    def segment(self) -> str:
        """The rollout segment's name."""
        return segment_name(self.slug)

    @property
    def included(self) -> list[str]:
        """The models that get the variant."""
        return [row.label for row in self.rows if row.included]

    @property
    def variant_value(self) -> str:
        """The variant's flag value, served to the segment."""
        return self.analysis.spec.variants[0].flag_value

    @property
    def control_value(self) -> str:
        """The control's flag value, which everyone else keeps."""
        return self.analysis.spec.control.flag_value

    def smoke_labels(self) -> list[tuple[str, ErrorStyle]]:
        """Each included model, then one excluded label, with the style it should be served.

        The excluded label is the first excluded model, or the ship smoke label when every
        model is included (that label is never part of the analysis).
        """
        variant = ErrorStyle.from_flag_value(self.variant_value)
        control = ErrorStyle.from_flag_value(self.control_value)
        excluded = next((r.label for r in self.rows if not r.included), SMOKE_AGENT_LABEL)
        return [*((label, variant) for label in self.included), (excluded, control)]

    def changes(self) -> list[str]:
        """The exact changes, in the order they are made."""
        spec = self.analysis.spec
        segment = (
            f"reuse segment {self.segment} (id {self.existing_segment_id}), which already "
            f"matches {TRAIT} in {self.included}"
            if self.existing_segment_id is not None
            else f"create project segment {self.segment}: {TRAIT} in {self.included}"
        )
        return [
            segment,
            f"in {spec.environment}, override flag {spec.flag} for that segment: enabled, value "
            f"{self.variant_value} ({spec.variants[0].key}), every variant weight 0",
            f"everyone else keeps {spec.control.key}: {self.control_value} (unchanged)",
        ]


def plan_per_model(
    analysis: Analysis,
    experiment: dict[str, Any],
    feature_state: dict[str, Any],
    segment: dict[str, Any] | None,
    results_file: Path,
) -> PerModelPlan:
    """The per-model plan, or `ShipRefusedError` when it cannot be applied safely.

    It needs the experiment ended (the flag is locked while it runs) and the flag serving
    control to everyone, so the override is the only place the variant is served.
    """
    spec, state = analysis.spec, analysis.state
    status = experiment.get("status")
    if status != COMPLETED:
        raise ShipRefusedError(
            f"experiment {spec.slug} is {status!r}, not {COMPLETED!r}; the flag is locked while "
            f"it runs, so end it first with `sa ship {spec.slug}`"
        )
    options = {state.variant_ids[arm.key]: arm.flag_value for arm in spec.variants}
    served = served_value(feature_state, options)
    if not feature_state.get("enabled") or served != spec.control.flag_value:
        raise ShipRefusedError(
            f"flag {spec.flag} in {spec.environment} serves {served!r} (enabled "
            f"{feature_state.get('enabled')!r}), not control to everyone; a per-model rollout "
            "only adds the variant for some models on top of control"
        )
    plan = PerModelPlan(analysis, results_file, model_rows(analysis))
    if segment is None:
        return plan
    matches = labels_in(segment.get("rules"))
    if matches != set(plan.included):
        raise ShipRefusedError(
            f"segment {plan.segment} (id {segment.get('id')}) already exists and matches "
            f"{sorted(matches) if matches is not None else 'other rules'}, not "
            f"{plan.included}; delete it in Flagsmith before rolling out again"
        )
    return PerModelPlan(analysis, results_file, plan.rows, int(segment["id"]))


@dataclass
class PerModelOutcome:
    """What was created, what was read back, and what the smoke sessions saw."""

    segment_id: int | None = None
    observed: dict[str, ErrorStyle | None] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def apply(plan: PerModelPlan, admin: FlagsmithAdmin, outcome: PerModelOutcome) -> None:
    """Create (or reuse) the segment and set the override; raises `FlagsmithAdminError`."""
    spec = plan.analysis.spec
    if plan.existing_segment_id is not None:
        outcome.segment_id = plan.existing_segment_id
    else:
        description = (
            f"Second Audience per-model rollout of {spec.variants[0].key} for {spec.slug} "
            f"({EXPLORATORY})"
        )
        created = admin.create_segment(plan.segment, rules_for(plan.included), description)
        outcome.segment_id = int(created["id"])
    admin.set_segment_override(
        spec.flag,
        outcome.segment_id,
        plan.variant_value,
        [arm.key for arm in spec.variants],
        plan.analysis.state.environment_key,
    )


def verify(plan: PerModelPlan, admin: FlagsmithAdmin, outcome: PerModelOutcome) -> None:
    """Read the segment and the override back; a failed read or a mismatch is a problem."""
    spec = plan.analysis.spec
    if outcome.segment_id is None:
        outcome.problems.append("no segment was created")
        return
    try:
        segment = admin.get_segment(plan.segment)
        override = admin.segment_override(
            spec.flag, outcome.segment_id, plan.analysis.state.environment_key
        )
    except Exception as error:  # any CLI, API or parsing failure after the change
        outcome.problems.append(f"read-back failed: {type(error).__name__}: {error}")
        return
    labels = labels_in(segment.get("rules")) if segment else None
    if labels != set(plan.included):
        outcome.problems.append(
            f"segment {plan.segment} matches {sorted(labels) if labels else labels!r}, "
            f"expected {plan.included}"
        )
    if not override.get("enabled"):
        outcome.problems.append(f"the segment override of {spec.flag} is disabled")
    if override.get("value") != plan.variant_value:
        outcome.problems.append(
            f"the segment override serves {override.get('value')!r}, "
            f"expected {plan.variant_value!r}"
        )
    weights = [float(v.get("weight", 0)) for v in override.get("variants") or []]
    if any(weights):
        outcome.problems.append(f"the segment override has variant weights {weights}, not 0")


# --- the table and the record ---------------------------------------------------------------


def _pct(interval: Interval | None) -> str:
    """A rate with its counts, or n/a."""
    if interval is None:
        return "n/a"
    return f"{interval.rate * 100:.1f}% ({interval.successes}/{interval.n})"


def table(plan: PerModelPlan) -> list[str]:
    """The per-model table as Markdown lines."""
    control, variant = plan.analysis.spec.control.key, plan.analysis.spec.variants[0].key
    lines = [
        f"| Model | n {control} | n {variant} | Correct {control} | Correct {variant} | "
        f"Wrong {control} | Wrong {variant} | Rollout |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in plan.rows:
        rollout = variant if row.included else f"{control} ({row.reason})"
        lines.append(
            f"| {row.label} | {row.control.n} | {row.variant.n} | {_pct(row.control.correct)} | "
            f"{_pct(row.variant.correct)} | {_pct(row.control.wrong)} | "
            f"{_pct(row.variant.wrong)} | {rollout} |"
        )
    return lines


def audit_detail(plan: PerModelPlan, outcome: PerModelOutcome) -> dict[str, Any]:
    """The `audit_log` detail: the models, the numbers per model, and what was verified."""

    def cell(c: Cell) -> dict[str, Any]:
        return {
            "n": c.n,
            "correct": c.correct.successes if c.correct else 0,
            "wrong": c.wrong.successes if c.wrong else 0,
        }

    return {
        "exploratory": EXPLORATORY,
        "rule": RULE,
        "results_file": plan.results_file.as_posix(),
        "included": plan.included,
        "excluded": [row.label for row in plan.rows if not row.included],
        "models": {
            row.label: {
                plan.analysis.spec.control.key: cell(row.control),
                plan.analysis.spec.variants[0].key: cell(row.variant),
                "included": row.included,
                "reason": row.reason,
            }
            for row in plan.rows
        },
        "segment": plan.segment,
        "segment_id": outcome.segment_id,
        "served_value": plan.variant_value,
        "smoke": {
            label: style.model_dump() if style else None
            for label, style in outcome.observed.items()
        },
        "problems": outcome.problems,
    }


def record(plan: PerModelPlan, outcome: PerModelOutcome, approved_by: str, now: datetime) -> str:
    """The dated per-model section added after the report's Decision section."""
    spec = plan.analysis.spec
    lines = [
        f"## Per-model rollout ({now.astimezone(UTC):%Y-%m-%d %H:%M} UTC), {EXPLORATORY}",
        "",
        f"**Exploratory:** per-model results are {EXPLORATORY}; they were not pre-registered as "
        "decision criteria and support no claim beyond the rows below.",
        "",
        f"- Applied by `sa ship {plan.slug} --per-model` and approved by {approved_by} (typed "
        f"`{confirmation(plan.slug)}`), from results `{plan.results_file.as_posix()}`.",
        f"- Rule: {RULE}.",
        "",
        *table(plan),
        "",
        f"- Segment `{plan.segment}` (id {outcome.segment_id}): `{TRAIT}` in "
        f"{', '.join(f'`{label}`' for label in plan.included)}. In {spec.environment}, flag "
        f"`{spec.flag}` serves it `{plan.variant_value}`; everyone else keeps "
        f"`{plan.control_value}`.",
    ]
    for label, style in outcome.observed.items():
        seen = (
            ", ".join(f"{name} {value}" for name, value in style.model_dump().items())
            if style
            else "not observed"
        )
        lines.append(f"- Smoke session labelled `{label}`: error payload showed {seen}.")
    if outcome.problems:
        lines += ["- **Verification failed:**", *(f"  - {p}" for p in outcome.problems)]
    else:
        lines.append(
            "- Verified: segment and override read back as planned, and each smoke session "
            "was served the expected style."
        )
    return "\n".join(lines) + "\n"


def append_record(folder: Path, text: str) -> Path:
    """Add the record to `per-model.md` and to `report.md`; returns the report path."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in (PER_MODEL_FILE, "report.md"):
        path = folder / name
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        joined = existing.rstrip("\n") + "\n\n" + text if existing else text
        path.write_text(joined, encoding="utf-8", newline="\n")
    return folder / "report.md"
