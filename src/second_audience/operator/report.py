"""`sa report`: the nine-section Markdown report, and `sa status`: the interim view.

Every number here comes from an `Analysis` (Flagsmith results, `manual.yaml`, ledger rows).
The only model-written text is the prose of sections 1 and 9, and the report says so.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from second_audience.operator.analysis import (
    FROM_DASHBOARD,
    SMALL_CELL,
    Analysis,
    Cell,
    MetricRow,
    in_experiment,
)
from second_audience.operator.narrative import Written
from second_audience.operator.stats import SRM_ALPHA, Interval

HYPOTHESIS_LINK = "../../HYPOTHESIS.md"
"""From `reports/<slug>/report.md` to the pre-registration."""

EXPLORATORY = "Exploratory: not pre-registered as decision criteria; small samples."

FIXED_LIMITATIONS = (
    "Lab traffic comes from two small Gemini Flash-Lite models on free quota; results may not "
    "carry over to larger models or other providers.",
    "Twelve synthetic scenarios in one fictional office; the trap share (5 of 12) is a design "
    "choice, not an estimate of how often real suggestions are wrong.",
    "Flagsmith's results include lab and field sessions together (its results cannot be split "
    "by trait); the breakdowns by source come from the ledger.",
    "The primary metric counts tool calls, not outcomes; the two guardrails watch outcomes.",
    "Breakdowns are exploratory and were not used for the decision.",
)


def _num(value: float | None, digits: int = 2) -> str:
    """Integers plainly, other values to `digits` decimals, missing values as a dash."""
    if value is None:
        return "n/a"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.{digits}f}"


def _pct(value: float) -> str:
    """A proportion as a percentage with one decimal."""
    return f"{value * 100:.1f}%"


def _rate(interval: Interval | None) -> str:
    """`rate (low to high)` as percentages, or n/a."""
    if interval is None:
        return "n/a"
    return f"{_pct(interval.rate)} ({_pct(interval.low)} to {_pct(interval.high)})"


def _table(header: Iterable[str], rows: Iterable[Iterable[object]]) -> list[str]:
    """A Markdown table."""
    head = list(header)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def _when(value: datetime | None) -> str:
    """UTC timestamp to the second."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "n/a"


# --- sections -------------------------------------------------------------------------------


def _summary(written: Written) -> list[str]:
    return ["## 1. Summary", "", written.narrative.summary]


def _setup(a: Analysis, results_file: str) -> list[str]:
    spec, rule = a.spec, a.spec.stopping_rule
    lines = [
        "## 2. Setup",
        "",
        f"- Question: {spec.hypothesis}",
        f"- Pre-registration: [HYPOTHESIS.md]({HYPOTHESIS_LINK}), frozen at commit "
        f"`{a.state.commit}`",
        f"- Flagsmith: environment `{spec.environment}`, flag `{spec.flag}`, experiment "
        f"{a.state.experiment_id}",
        f"- Started: {_when(a.state.started_at)}; hard end: {_when(rule.hard_end)}",
        f"- Stopping rule: at least {rule.min_exposed_per_arm} exposed sessions per arm, or "
        f"the hard end; ship only if the win probability on the primary metric is at least "
        f"{rule.win_probability_threshold:g} and no guardrail is credibly worse",
        f"- Results: Flagsmith as of {_when(a.results.as_of)}, saved in `{results_file}`; "
        f"report generated {_when(a.now)}",
        "",
    ]
    lines += _table(
        ["Arm", "Variant key", "Split", "Flag value"],
        (
            ["Informative" if arm.key == "control" else arm.key.capitalize(), f"`{arm.key}`",
             f"{spec.allocation.split[arm.key]:g}%", f"`{arm.flag_value}`"]
            for arm in spec.arms
        ),
    )  # fmt: skip
    lines.append("")
    lines += _table(
        ["Role", "Metric", "Event", "Aggregation", "Expected direction"],
        (
            [m.role, m.name, f"`{m.event}`", m.aggregation, m.expected_direction]
            for m in spec.metrics
        ),
    )
    return lines


def _headline_row(a: Analysis, row: MetricRow) -> list[str]:
    stat = "rate" if row.metric.aggregation == "occurrence" else row.metric.aggregation
    lines = [f"**{row.metric.role.capitalize()}: {row.metric.name}** (`{row.metric.event}`)", ""]
    lines += _table(
        ["Arm", "Exposed (n)", "Sum", f"{stat.capitalize()} (sum / n)"],
        (
            [arm, s.n, _num(s.sum), _num(s.mean, 3)] if s else [arm, "n/a", "n/a", "n/a"]
            for arm, s in row.stats.items()
        ),
    )
    variant = a.spec.variants[0].key
    lines.append("")
    if not a.decision.rule_met:
        lines.append(
            "Lift, credible interval and win probability are withheld until the stopping rule "
            "is met."
        )
    elif row.inference is None:
        lines.append(
            "No inference from Flagsmith: it needs at least 50 exposed identities and 5 "
            "conversions per arm for each metric."
        )
    else:
        inf = row.inference
        source = " (from dashboard)" if row.inference_source == FROM_DASHBOARD else ""
        lines.append(
            f"{variant} against control{source}: lift {_num(inf.lift, 3)}, 95% credible "
            f"interval {_num(inf.ci_low, 3)} to {_num(inf.ci_high, 3)}, win probability "
            f"{_num(inf.chance_to_win, 3)}."
        )
    return [*lines, ""]


def _headline(a: Analysis) -> list[str]:
    lines = ["## 3. Headline result (from Flagsmith)", ""]
    for row in a.rows:
        lines += _headline_row(a, row)
    return lines


def _reconciliation(a: Analysis) -> list[str]:
    lines = [
        "## 4. Reconciliation",
        "",
        'Ledger counts against Flagsmith\'s, per arm. "Ledger at as_of" counts only what the '
        "ledger had recorded by Flagsmith's as_of time, which separates event latency from "
        "lost events.",
        "",
    ]
    lines += _table(
        ["Measure", "Arm", "Ledger", "Ledger at as_of", "Flagsmith", "Difference", "Likely cause"],
        (
            [r.measure, r.arm, _num(r.ledger), _num(r.ledger_at_as_of), _num(r.flagsmith),
             _num(r.difference), r.cause]
            for r in a.reconciliation
        ),
    )  # fmt: skip
    fallback = sum(t.fallback for t in a.traffic)
    still_open = sum(1 for r in a.records if in_experiment(r) and r.outcome is None)
    lines += [
        "",
        f"Sessions exposed while Flagsmith was unreachable (fallback, excluded from both "
        f"sides by design): {fallback}. Enrolled sessions still open (no events sent yet): "
        f"{still_open}.",
    ]
    return lines


def _split_health(a: Analysis) -> list[str]:
    lines = ["## 5. Split health", ""]
    if a.srm is None:
        return [*lines, "No exposed sessions yet."]
    lines += _table(
        ["Arm", "Configured", "Expected", "Observed (Flagsmith)"],
        (
            [arm, f"{a.spec.allocation.split[arm]:g}%", _num(a.srm.expected[arm]), observed]
            for arm, observed in a.srm.observed.items()
        ),
    )
    verdict = (
        f"sample-ratio mismatch (p < {SRM_ALPHA:g}); treat the result with caution"
        if a.srm.mismatch
        else f"no sample-ratio mismatch at p < {SRM_ALPHA:g}"
    )
    flagsmith = (
        _num(a.results.srm_p_value, 4) if a.results.srm_p_value is not None else "not reported"
    )
    lines += [
        "",
        f"Chi-square (1 degree of freedom) = {_num(a.srm.chi_square, 3)}, p = "
        f"{_num(a.srm.p_value, 4)}: {verdict}. Flagsmith's own SRM p-value: {flagsmith}.",
    ]
    return lines


def _cells(cells: list[Cell]) -> list[str]:
    return _table(
        ["Group", "Arm", "n", "Correct (95% CI)", "Wrong booking (95% CI)", "Mean tool calls"],
        (
            [c.group, c.arm, c.n, _rate(c.correct), _rate(c.wrong), _num(c.mean_tool_calls)]
            for c in cells
        ),
    )


def _breakdowns(a: Analysis) -> list[str]:
    lines = [
        "## 6. Breakdowns (ledger)",
        "",
        f"**{EXPLORATORY}**",
        "",
        "Scored sessions only: exposed, in the experiment, and finished (not abandoned). Rates "
        "use the final booking state; intervals are 95% Wilson intervals.",
    ]
    for title, cells in a.breakdowns.items():
        lines += ["", f"### {title}", ""]
        lines += _cells(cells) if cells else ["No scored sessions."]
    return lines


def _traffic(a: Analysis) -> list[str]:
    lines = ["## 7. Traffic", "", f"All sessions started since {_when(a.state.started_at)}.", ""]
    lines += _table(
        ["Source", "Model", "Sessions", "Exposed", "Share exposed", "In experiment",
         "Excluded (Flagsmith unreachable)"],
        (
            [t.source, t.model, t.sessions, t.exposed, _pct(t.share_exposed), t.enrolled,
             t.fallback]
            for t in a.traffic
        ),
    )  # fmt: skip
    lines.append("")
    names = list(a.traffic[0].outcomes) if a.traffic else []
    lines += _table(
        ["Source", "Model", *names],
        ([t.source, t.model, *t.outcomes.values()] for t in a.traffic),
    )
    return lines


def _limitations(a: Analysis) -> list[str]:
    computed = []
    if a.small_cells:
        computed.append(
            f"Breakdown cells with fewer than {SMALL_CELL} scored sessions: "
            + "; ".join(a.small_cells)
            + "."
        )
    mismatched = [
        r for r in a.reconciliation if r.cause not in ("match", "no Flagsmith results yet")
    ]
    if mismatched:
        computed.append(
            "The ledger and Flagsmith differ on "
            + ", ".join(f"{r.measure} / {r.arm}" for r in mismatched)
            + " (section 4)."
        )
    if a.srm is not None and a.srm.mismatch:
        computed.append("The observed split differs from the configured split (section 5).")
    if any(r.inference_source == FROM_DASHBOARD for r in a.rows):
        computed.append("Some inference values were copied from the Flagsmith dashboard.")
    missing = [r.metric.name for r in a.rows if r.inference is None]
    if missing:
        computed.append(f"Flagsmith returned no inference for: {', '.join(missing)}.")
    field = sum(t.sessions for t in a.traffic if t.source == "field")
    if field:
        computed.append(f"Field sessions included in Flagsmith's counts: {field}.")
    if not a.decision.rule_met:
        computed.append("The stopping rule is not met: this is an interim report.")
    return ["## 8. Limitations", "", *(f"- {item}" for item in (*FIXED_LIMITATIONS, *computed))]


def _decision(a: Analysis, written: Written) -> list[str]:
    decision = a.decision.decision
    lines = [
        "## 9. Decision",
        "",
        f"**Decision: {decision.value}.**",
        "",
        written.narrative.decision,
        "",
        "Rule applied (pre-registered in HYPOTHESIS.md section 10):",
        "",
        *(f"- {reason}" for reason in a.decision.reasons),
    ]
    if a.decision.rule_met and any(r.inference is None for r in a.rows[1:]):
        variant = a.spec.variants[0].key
        if a.trap_wrong:
            lines += ["", f"Wrong bookings by {variant} on trap scenarios (ledger):", ""]
            lines += [
                f"- `{r.session_id}` scenario {r.scenario_id}, {r.agent_label}"
                for r in a.trap_wrong
            ]
        else:
            lines += ["", f"No wrong bookings by {variant} on trap scenarios (ledger)."]
    return lines


def render(
    a: Analysis, written: Written, results_file: str, note: str, shipped: str | None = None
) -> str:
    """The full Markdown report; `shipped` is the dated record `sa ship` wrote, if any."""
    lines = [
        f"# Report: {a.spec.slug}",
        "",
        f"Decision: **{a.decision.decision.value}**. Results {note}.",
        "",
        f"Prose in sections 1 and 9: {written.author}. Every other line, and every number, is "
        "computed from the saved Flagsmith results, `manual.yaml` and the ledger.",
        "",
    ]
    sections: list[Callable[[], list[str]]] = [
        lambda: _summary(written),
        lambda: _setup(a, results_file),
        lambda: _headline(a),
        lambda: _reconciliation(a),
        lambda: _split_health(a),
        lambda: _breakdowns(a),
        lambda: _traffic(a),
        lambda: _limitations(a),
        lambda: _decision(a, written),
    ]
    for section in sections:
        lines += [*section(), ""]
    if shipped is not None:
        lines += [
            f"Sections 1 to 9 use `{results_file}`. The record below is the decision recorded "
            "at ship from the results it names, and is the one applied; if section 9 differs, "
            "it is because those results have since been refreshed.",
            "",
            shipped.rstrip("\n"),
            "",
        ]
    return "\n".join(lines)


# --- status ---------------------------------------------------------------------------------


def status_lines(
    a: Analysis, experiment_status: str, ledger_exposed: dict[str, int], peek: bool
) -> list[str]:
    """`sa status`: enrolment and progress; inference only once the rule is met, or on peek."""
    spec, rule = a.spec, a.spec.stopping_rule
    left = rule.hard_end - a.now
    remaining = (
        "passed"
        if left.total_seconds() <= 0
        else f"{left.days}d {left.seconds // 3600}h {left.seconds % 3600 // 60}m left"
    )
    width = rule.min_exposed_per_arm
    lines = [
        f"Experiment {spec.slug} (id {a.state.experiment_id}, {spec.environment}): "
        f"{experiment_status}",
        f"Hard end {_when(rule.hard_end)}: {remaining}",
        f"Exposed per arm, Flagsmith (as of {_when(a.results.as_of)}): "
        + ", ".join(f"{arm} {n}/{width}" for arm, n in a.exposures.items()),
        "Exposed per arm, ledger (live): "
        + ", ".join(f"{arm} {n}/{width}" for arm, n in ledger_exposed.items()),
        "Ledger since start: "
        + "; ".join(
            f"{source} {sum(t.sessions for t in a.traffic if t.source == source)} sessions, "
            f"{sum(t.enrolled for t in a.traffic if t.source == source)} in experiment"
            for source in sorted({t.source for t in a.traffic})
        ),
    ]
    if a.decision.rule_met:
        lines.append("Stopping rule: met. Run `sa report` for the decision.")
    else:
        lines.append(
            "Stopping rule: not met (Flagsmith's counts decide). No win probability or leader "
            "is shown before it is."
        )
    if a.decision.rule_met or peek:
        if peek and not a.decision.rule_met:
            lines.append(
                "WARNING: peeking before the stopping rule is met. These numbers must not drive "
                "a decision; this look is recorded in the audit log."
            )
        variant = spec.variants[0].key
        for row in a.rows:
            if row.inference is None:
                lines.append(f"  {row.metric.name}: no inference yet")
            else:
                lines.append(
                    f"  {row.metric.name}: {variant} win probability "
                    f"{row.inference.chance_to_win:.3f}, lift {row.inference.lift:.3f} "
                    f"[{row.inference.ci_low:.3f}, {row.inference.ci_high:.3f}]"
                )
    return lines


def report_dir(slug: str) -> Path:
    """`reports/<slug>/`, relative to the working directory (the repository root)."""
    return Path("reports") / slug
