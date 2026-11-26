"""`sa ship`: apply the pre-registered decision, end the experiment, verify, and record it.

Ending an experiment is one documented Admin API call, verified live on Development in the
SA-02 spike (question 8): `POST .../experiments/<id>/complete/` with `rollout_decision`
`variant` (and the winning option's ID) or `control`. Nothing here runs until the human has
seen the plan and typed the confirmation. The decision is recorded as soon as Flagsmith
accepts the change. Then the experiment and the flag are read back, one scripted session
against the server confirms the style it now serves, and the record is completed with what
was verified. A failure after the change is a recorded problem, never a missing record.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from second_audience.booking.scenarios import get_scenario
from second_audience.booking.sessions import AGENT_LABEL_HEADER, LAB_KEY_HEADER
from second_audience.config import Settings
from second_audience.core.styles import ErrorStyle
from second_audience.operator.analysis import (
    SMOKE_AGENT_LABEL,
    Analysis,
    Decision,
    event_value,
    scored,
)
from second_audience.operator.flagsmith_admin import FlagsmithAdmin
from second_audience.operator.start import RUNNING

COMPLETED = "completed"
DECISION_FILE = "decision.md"

SMOKE_SCENARIO = "s01"
"""Aligned: its first attempt hits a conflict that has a suggestion, so every style shows."""
SMOKE_ATTEMPTS = 6
SMOKE_WAIT_SECONDS = 5.0
"""A flag change takes 3 to 6 seconds to reach every edge node (spike findings, SA-06)."""
SMOKE_TIMEOUT_SECONDS = 30


class ShipRefusedError(RuntimeError):
    """`sa ship` will not proceed; nothing was changed."""


@dataclass(frozen=True)
class ShipPlan:
    """The decision and the one Flagsmith change that carries it out."""

    analysis: Analysis
    results_file: Path
    rollout_decision: str
    rollout_variant: int | None
    serve_arm: str
    serve_value: str

    @property
    def decision(self) -> Decision:
        """The decision the rule produced."""
        return self.analysis.decision.decision

    def changes(self) -> list[str]:
        """The exact changes, in the order `apply_ship` makes them."""
        spec, state = self.analysis.spec, self.analysis.state
        body: dict[str, Any] = {"rollout_decision": self.rollout_decision}
        if self.rollout_variant is not None:
            body["rollout_variant"] = self.rollout_variant
        flag = (
            f"flag {spec.flag} in {spec.environment} serves {self.serve_arm} to everyone: "
            f"{self.serve_value}"
        )
        if self.serve_arm == spec.control.key:
            flag += " (the current default value, unchanged)"
        return [
            f"POST experiments/{state.experiment_id}/complete/ {json.dumps(body)}: ends "
            f"experiment {spec.slug} (irreversible)",
            flag,
        ]


def plan_ship(analysis: Analysis, experiment: dict[str, Any], results_file: Path) -> ShipPlan:
    """The ship plan, or `ShipRefusedError` if the experiment is not running or not ready."""
    spec, state = analysis.spec, analysis.state
    status = experiment.get("status")
    if status == COMPLETED:
        raise ShipRefusedError(
            f"experiment {spec.slug} already ended at {experiment.get('ended_at')} with "
            f"rollout_decision {experiment.get('rollout_decision')!r}"
        )
    if status != RUNNING:
        raise ShipRefusedError(f"experiment {spec.slug} is {status!r}, not running")
    if not analysis.decision.rule_met:
        raise ShipRefusedError(
            "the stopping rule is not met; the decision is taken once, at the stopping point"
        )
    if analysis.decision.decision is Decision.SHIP_VARIANT:
        arm = spec.variants[0]
        return ShipPlan(
            analysis, results_file, "variant", state.variant_ids[arm.key], arm.key, arm.flag_value
        )
    control = spec.control
    return ShipPlan(analysis, results_file, "control", None, control.key, control.flag_value)


def served_value(feature_state: dict[str, Any], option_values: dict[int, str]) -> str | None:
    """The value every identity gets from an environment feature state, or `None` for a split.

    One variant at 100% serves that variant; every variant at 0% serves the default value.
    """
    allocations = {
        int(mv["multivariate_feature_option"]): float(mv["percentage_allocation"])
        for mv in feature_state.get("multivariate_feature_state_values", [])
    }
    full = [option for option, share in allocations.items() if share >= 100]
    if len(full) == 1:
        return option_values.get(full[0])
    if all(share == 0 for share in allocations.values()):
        return str(feature_state.get("feature_state_value"))
    return None


@dataclass
class Outcome:
    """What Flagsmith answered, what was read back after the change, and what did not match."""

    experiment: dict[str, Any]
    served: str | None = None
    observed: ErrorStyle | None = None
    problems: list[str] = field(default_factory=list)
    read_back: bool = False
    """The experiment and the flag were read back after the change."""
    checked: bool = False
    """Verification has finished (or was cut short, which `problems` says)."""

    @property
    def smoke_safe(self) -> bool:
        """Whether the read-back shows the experiment ended, so a smoke session cannot enrol."""
        return self.read_back and self.experiment.get("status") == COMPLETED


def complete(plan: ShipPlan, admin: FlagsmithAdmin) -> Outcome:
    """End the experiment (irreversible); the outcome holds Flagsmith's answer, unverified."""
    state = plan.analysis.state
    try:
        answer = admin.complete_experiment(
            state.environment_key, state.experiment_id, plan.rollout_decision, plan.rollout_variant
        )
    except json.JSONDecodeError as error:  # the CLI exited cleanly, so the call was made
        return Outcome({}, problems=[f"complete/ answered with unreadable JSON: {error}"])
    return Outcome(dict(answer or {}))


def read_back(plan: ShipPlan, admin: FlagsmithAdmin, outcome: Outcome) -> None:
    """Read the experiment and the flag back and note anything that does not match the plan.

    The change is already made, so a failed read becomes a problem, not an exception.
    """
    spec, state = plan.analysis.spec, plan.analysis.state
    env = state.environment_key
    try:
        experiment = admin.get_experiment(env, state.experiment_id)
        feature_state = admin.feature_state(env, state.feature_id)
    except Exception as error:  # any CLI, API or parsing failure after the irreversible call
        outcome.problems.append(f"read-back failed: {type(error).__name__}: {error}")
        return
    options = {state.variant_ids[arm.key]: arm.flag_value for arm in spec.variants}
    outcome.experiment, outcome.read_back = experiment, True
    outcome.served = served_value(feature_state, options)
    if experiment.get("status") != COMPLETED:
        outcome.problems.append(f"experiment status is {experiment.get('status')!r}")
    if experiment.get("rollout_decision") != plan.rollout_decision:
        outcome.problems.append(
            f"rollout_decision is {experiment.get('rollout_decision')!r}, "
            f"expected {plan.rollout_decision!r}"
        )
    if not feature_state.get("enabled"):
        outcome.problems.append(f"flag {spec.flag} is disabled")
    if outcome.served != plan.serve_value:
        outcome.problems.append(
            f"flag {spec.flag} serves {outcome.served!r}, expected {plan.serve_value!r}"
        )


# --- the smoke session ----------------------------------------------------------------------


def style_of(payload: dict[str, Any]) -> ErrorStyle:
    """The error style an error payload shows (booking/errors.py `render_error`)."""
    return ErrorStyle(
        suggest_alternative="suggestion" in payload,
        corrected_call="corrected_call" in payload,
        caution_note="note" in payload,
    )


async def _call(client: Client[Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool and return its structured payload."""
    result = await client.call_tool(tool, arguments)
    if not isinstance(result.structured_content, dict):
        raise RuntimeError(f"{tool} returned no structured content")
    return result.structured_content


async def observe_style(client: Client[Any]) -> ErrorStyle:
    """One scripted session on a connected client: begin, fail once, finish; the error's style."""
    scenario = get_scenario(SMOKE_SCENARIO)
    started = await _call(client, "begin_task", {"scenario_id": scenario.id})
    session_id = str(started["session_id"])
    payload = await _call(client, "book_room", scenario.first_attempt.tool_arguments(session_id))
    await _call(client, "finish_task", {"session_id": session_id, "summary": "sa ship smoke check"})
    if "error" not in payload:
        raise RuntimeError(f"scenario {scenario.id}'s first attempt did not fail")
    return style_of(payload)


def smoke_session(settings: Settings) -> ErrorStyle:
    """Run one smoke session against `SA_SERVER_URL`, as lab traffic (`SA_LAB_KEY`)."""
    if settings.sa_server_url is None or settings.sa_lab_key is None:
        raise RuntimeError("SA_SERVER_URL and SA_LAB_KEY must both be set")
    headers = {
        AGENT_LABEL_HEADER: SMOKE_AGENT_LABEL,
        LAB_KEY_HEADER: settings.sa_lab_key.get_secret_value(),
    }
    transport = StreamableHttpTransport(settings.sa_server_url, headers=headers)

    async def run() -> ErrorStyle:
        async with Client(transport, timeout=SMOKE_TIMEOUT_SECONDS) as client:
            return await observe_style(client)

    return asyncio.run(run())


def smoke_until(
    probe: Callable[[], ErrorStyle], expected: ErrorStyle, sleep: Callable[[float], None]
) -> ErrorStyle:
    """Probe until the served style is `expected`, waiting for edge propagation first.

    The first wait matters: until the change reaches the edge, a session can still be
    enrolled in the experiment that just ended.
    """
    sleep(SMOKE_WAIT_SECONDS)
    observed = probe()
    for _ in range(SMOKE_ATTEMPTS - 1):
        if observed == expected:
            break
        sleep(SMOKE_WAIT_SECONDS)
        observed = probe()
    return observed


# --- the record -----------------------------------------------------------------------------


def _inference(analysis: Analysis) -> dict[str, dict[str, float] | None]:
    """Each metric's inference, as the decision used it."""
    return {
        row.metric.name: row.inference.model_dump() if row.inference else None
        for row in analysis.rows
    }


def guardrail_ledger_counts(analysis: Analysis) -> dict[str, dict[str, dict[str, int]]]:
    """Per-arm ledger counts for each guardrail Flagsmith gave no inference on.

    HYPOTHESIS.md section 10 reports these instead of a lift interval.
    """
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for row in analysis.rows:
        if row.metric.role != "guardrail" or row.inference is not None:
            continue
        counts[row.metric.name] = {}
        for arm in analysis.spec.arms:
            done = [r for r in analysis.records if scored(r) and r.variant == arm.key]
            total = sum(event_value(r, row.metric.event) for r in done)
            counts[row.metric.name][arm.key] = {"scored": len(done), "count": int(total)}
    return counts


def audit_detail(plan: ShipPlan, outcome: Outcome) -> dict[str, Any]:
    """The `audit_log` detail: the decision, the numbers it rests on, and what was verified."""
    analysis = plan.analysis
    as_of = analysis.results.as_of
    ledger_counts = guardrail_ledger_counts(analysis)
    detail: dict[str, Any] = {
        "decision": plan.decision.value,
        "reasons": analysis.decision.reasons,
        "exposures": analysis.exposures,
        "inference": _inference(analysis),
        "results_file": plan.results_file.as_posix(),
        "results_as_of": as_of.isoformat() if as_of else None,
        "rollout_decision": plan.rollout_decision,
        "rollout_variant": plan.rollout_variant,
        "experiment_status": outcome.experiment.get("status"),
        "ended_at": outcome.experiment.get("ended_at"),
        "served_arm": plan.serve_arm,
        "served_value": outcome.served,
        "smoke_style": outcome.observed.model_dump() if outcome.observed else None,
        "verification": "done" if outcome.checked else "pending",
        "problems": outcome.problems,
    }
    if ledger_counts:
        detail["guardrail_ledger_counts"] = ledger_counts
        detail["trap_wrong_bookings"] = [r.session_id for r in analysis.trap_wrong]
    return detail


def decision_record(plan: ShipPlan, outcome: Outcome, approved_by: str, now: datetime) -> str:
    """The dated "Decision" section appended to the report."""
    spec, state = plan.analysis.spec, plan.analysis.state
    as_of = plan.analysis.results.as_of
    variant = f" (option {plan.rollout_variant})" if plan.rollout_variant is not None else ""
    observed = (
        ", ".join(f"{name} {value}" for name, value in outcome.observed.model_dump().items())
        if outcome.observed
        else "not observed"
    )
    lines = [
        f"## Decision ({now.astimezone(UTC):%Y-%m-%d %H:%M} UTC)",
        "",
        f"- Decision: **{plan.decision.value}**, applied by `sa ship` and approved by "
        f"{approved_by} (typed `ship {spec.slug}`).",
        f"- Results: `{plan.results_file.as_posix()}`, Flagsmith as of "
        f"{as_of.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC."
        if as_of
        else "- Results: none.",
        "- Rule applied (HYPOTHESIS.md section 10):",
        *(f"  - {reason}" for reason in plan.analysis.decision.reasons),
        f"- Flagsmith: experiment {state.experiment_id} is {outcome.experiment.get('status')} "
        f"(ended {outcome.experiment.get('ended_at')}), rollout_decision "
        f"`{plan.rollout_decision}`{variant}.",
    ]
    if not outcome.checked:
        lines.append(
            "- **Verification pending:** Flagsmith accepted the change; the read-back and the "
            "smoke session had not finished when this was written. If this line remains, "
            "`sa ship` stopped before verifying: check the experiment and the flag by hand."
        )
    else:
        served = f"`{outcome.served}`" if outcome.read_back else "not read back"
        lines += [
            f"- Flag `{spec.flag}` in {spec.environment} now serves {served} "
            f"(expected {plan.serve_arm}: `{plan.serve_value}`), read back from the environment.",
            f"- Smoke session on scenario {SMOKE_SCENARIO}: error payload showed {observed}.",
        ]
    if outcome.problems:
        lines += ["- **Verification failed:**", *(f"  - {p}" for p in outcome.problems)]
    elif outcome.checked:
        lines.append("- Verified: experiment ended, flag value and served style match.")
    return "\n".join(lines) + "\n"


def append_record(folder: Path, record: str, replacing: str | None = None) -> Path:
    """Save the record as `decision.md` and add it to `report.md`; returns the report path.

    With `replacing`, an earlier record already in the report is replaced in place.
    """
    folder.mkdir(parents=True, exist_ok=True)
    (folder / DECISION_FILE).write_text(record, encoding="utf-8", newline="\n")
    report = folder / "report.md"
    existing = report.read_text(encoding="utf-8") if report.exists() else ""
    if replacing is not None and replacing in existing:
        text = existing.replace(replacing, record)
    else:
        text = existing.rstrip("\n") + "\n\n" + record
    report.write_text(text, encoding="utf-8", newline="\n")
    return report
