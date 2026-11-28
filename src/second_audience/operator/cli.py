"""`sa`: the operator CLI. Every change to Flagsmith needs a typed human confirmation."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from second_audience.config import Settings, get_settings
from second_audience.core.ledger import Ledger, make_engine
from second_audience.core.styles import ErrorStyle
from second_audience.operator.analysis import Analysis, RunState, analyse, ledger_exposures
from second_audience.operator.doctor import Report, run_checks
from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError
from second_audience.operator.gemini import JsonModel, gemini_json
from second_audience.operator.narrative import Drafter, gemini_drafter, write_narrative
from second_audience.operator.propose import (
    INSTRUCTIONS,
    Proposal,
    build_evidence,
    draft,
    rationale_markdown,
    spec_yaml,
    taken_slugs,
)
from second_audience.operator.repo import GitError, Repo
from second_audience.operator.report import render, report_dir, status_lines
from second_audience.operator.results import (
    ExperimentResults,
    latest_saved,
    load_manual,
    refresh_and_save,
)
from second_audience.operator.ship import (
    DECISION_FILE,
    Outcome,
    ShipPlan,
    ShipRefusedError,
    already_verified,
    append_record,
    audit_detail,
    complete,
    decision_record,
    guardrail_ledger_counts,
    pending_record,
    plan_ship,
    read_back,
    smoke_session,
    smoke_until,
)
from second_audience.operator.spec import ExperimentSpec, load_spec
from second_audience.operator.start import (
    Plan,
    StartRefusedError,
    apply_plan,
    make_plan,
    state_path,
    write_state,
)

DEFAULT_SPEC = Path("experiments/error-style-v1.yaml")
PROPOSALS = DEFAULT_SPEC.parent / "proposals"
HYPOTHESIS_FILE = "HYPOTHESIS.md"

app = typer.Typer(
    help="Second Audience operator: runs agent-interface experiments on Flagsmith.",
    no_args_is_help=True,
    add_completion=False,
)

SpecArgument = Annotated[Path, typer.Argument(help="Experiment spec file (YAML).")]
SlugArgument = Annotated[str, typer.Argument(help="Experiment slug (experiments/<slug>.yaml).")]


@dataclass(frozen=True)
class Context:
    """Everything the commands touch outside the process; tests swap in fakes."""

    load_settings: Callable[[], Settings]
    make_admin: Callable[[Settings], FlagsmithAdmin]
    checks: Callable[[Callable[[], Settings], Path, Callable[[Settings], FlagsmithAdmin]], Report]
    open_repo: Callable[[Path], Repo]
    open_ledger: Callable[[Settings], Ledger]
    now: Callable[[], datetime]
    make_drafter: Callable[[Settings], Drafter | None]
    sleep: Callable[[float], None]
    make_proposer: Callable[[Settings], JsonModel | None]
    smoke: Callable[[Settings], ErrorStyle]


def _drafter(settings: Settings) -> Drafter | None:
    """A Gemini drafter for the report's prose, or `None` without a model key."""
    if settings.gemini_api_key is None:
        return None
    return gemini_drafter(settings.gemini_api_key.get_secret_value(), settings.sa_operator_model)


def _proposer(settings: Settings) -> JsonModel | None:
    """A Gemini model that answers with a `Proposal`, or `None` without a model key."""
    if settings.gemini_api_key is None:
        return None
    return gemini_json(
        settings.gemini_api_key.get_secret_value(),
        settings.sa_operator_model,
        INSTRUCTIONS,
        Proposal.model_json_schema(),
    )


def make_context() -> Context:
    """The real context: settings from the environment, the `flagsmith` CLI, git, the ledger."""
    return Context(
        load_settings=get_settings,
        make_admin=lambda settings: FlagsmithAdmin(settings.sa_flagsmith_project),
        checks=run_checks,
        open_repo=Repo.discover,
        open_ledger=lambda settings: Ledger(make_engine(settings)),
        now=lambda: datetime.now(UTC),
        make_drafter=_drafter,
        sleep=time.sleep,
        make_proposer=_proposer,
        smoke=smoke_session,
    )


def _print_report(report: Report) -> None:
    """The doctor table: one row per check."""
    width = max(len(check.name) for check in report.checks)
    for check in report.checks:
        typer.echo(f"  {check.status.upper():<4}  {check.name:<{width}}  {check.detail}")


def _gate_problems(spec_path: Path, repo: Repo, now: datetime, plan: Plan) -> list[str]:
    """Reasons to refuse that git and the clock decide: pre-registration and the hard end."""
    problems = []
    hypothesis = repo.root / HYPOTHESIS_FILE
    uncommitted = repo.uncommitted([spec_path, hypothesis])
    if uncommitted:
        problems.append(f"commit these first, so git proves the plan came first: {uncommitted}")
    if not hypothesis.exists():
        problems.append(f"{HYPOTHESIS_FILE} does not exist")
    elif plan.spec.slug not in hypothesis.read_text(encoding="utf-8"):
        problems.append(f"{HYPOTHESIS_FILE} does not mention {plan.spec.slug}")
    if plan.spec.hard_end_passed(now) and not plan.already_running:
        problems.append(f"the hard end {plan.spec.stopping_rule.hard_end} has passed")
    return problems


def _print_plan(plan: Plan, problems: list[str]) -> None:
    """What will happen, where, and when the experiment may be read."""
    spec, rule = plan.spec, plan.spec.stopping_rule
    typer.echo(f"\nPlan for {spec.slug} in Flagsmith environment {spec.environment}:")
    for step in plan.steps():
        typer.echo(f"  - {step}")
    split = ", ".join(f"{key} {weight:g}%" for key, weight in spec.allocation.split.items())
    typer.echo(f"  traffic: {spec.allocation.traffic_percent:g}% of exposed sessions; {split}")
    typer.echo(
        f"  stopping rule: at least {rule.min_exposed_per_arm} exposed sessions per arm, then "
        f"ship only if win probability on {spec.primary_metric.event} >= "
        f"{rule.win_probability_threshold:g}; hard end {rule.hard_end.isoformat()}"
    )
    for problem in problems:
        typer.echo(f"  REFUSED: {problem}")


def _fail(message: str) -> typer.Exit:
    """Print a refusal and return the exit to raise."""
    typer.echo(f"\n{message}", err=True)
    return typer.Exit(code=1)


@app.command()
def doctor(spec: SpecArgument = DEFAULT_SPEC) -> None:
    """Check settings, Flagsmith CLI login, warehouse, database, model key and server."""
    context = make_context()
    report = context.checks(context.load_settings, spec, context.make_admin)
    _print_report(report)
    if not report.ok:
        raise _fail("doctor: at least one check failed")
    typer.echo("\ndoctor: all checks passed")


@app.command()
def start(
    spec: SpecArgument,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the plan and exit.")] = False,
) -> None:
    """Create (or reuse) the flag, variants, metrics and experiment, then start it."""
    context = make_context()
    report = context.checks(context.load_settings, spec, context.make_admin)
    _print_report(report)
    if not report.ok or report.spec is None or report.environment_key is None:
        raise _fail("start: doctor checks failed; nothing was changed")
    settings = context.load_settings()
    admin = context.make_admin(settings)
    try:
        repo = context.open_repo(spec.resolve().parent)
        plan = make_plan(report.spec, admin, report.environment_key)
        gate = _gate_problems(spec, repo, context.now(), plan)
    except (FlagsmithAdminError, GitError) as error:
        raise _fail(f"start: {error}") from error
    _print_plan(plan, [*gate, *plan.problems])
    if dry_run:
        typer.echo("\nDry run: nothing was changed.")
        return
    if gate or plan.problems:
        raise _fail("start: refused; nothing was changed")

    if not plan.already_running:
        typed = typer.prompt(f"\nType the slug ({plan.spec.slug}) to create and start it")
        if typed.strip() != plan.spec.slug:
            raise _fail("start: confirmation did not match; nothing was changed")
    try:
        started = apply_plan(plan, admin)
        approver = repo.user_name()
        state = write_state(
            state_path(spec, plan.spec.slug),
            plan,
            started,
            repo.head_commit(),
            approver,
            context.now,
        )
    except (FlagsmithAdminError, StartRefusedError, GitError) as error:
        raise _fail(f"start: {error}") from error

    if plan.already_running:
        typer.echo(
            f"\nExperiment {plan.spec.slug} is already running (id {state['experiment_id']})."
        )
        return
    context.open_ledger(settings).audit(
        "start", plan.spec.slug, json.dumps(state, sort_keys=True), approver
    )
    typer.echo(
        f"\nStarted experiment {plan.spec.slug} (id {state['experiment_id']}) at "
        f"{state['started_at']}, commit {state['commit'][:12]}, approved by {approver}."
    )


def _experiment(slug: str) -> tuple[ExperimentSpec, RunState]:
    """The spec and the state `sa start` recorded for this slug."""
    spec_path = DEFAULT_SPEC.parent / f"{slug}.yaml"
    try:
        spec = load_spec(spec_path)
        run_state = RunState.load(state_path(spec_path, slug))
    except FileNotFoundError as error:
        raise _fail(f"{slug}: {error.filename} not found; has `sa start` run?") from error
    except ValueError as error:
        raise _fail(f"{slug}: {error}") from error
    return spec, run_state


def _analyse(
    context: Context,
    settings: Settings,
    spec: ExperimentSpec,
    run_state: RunState,
    results: ExperimentResults,
) -> tuple[Analysis, Ledger]:
    """Combine Flagsmith results, the manual overlay and the ledger rows since the start."""
    ledger = context.open_ledger(settings)
    records = ledger.sessions_since(run_state.started_at)
    try:
        manual = load_manual(report_dir(spec.slug))
        return analyse(spec, run_state, results, manual, records, context.now()), ledger
    except ValueError as error:
        raise _fail(f"{spec.slug}: {error}") from error


@app.command()
def status(
    slug: SlugArgument,
    peek: Annotated[
        bool, typer.Option("--peek", help="Show inference before the stopping rule is met.")
    ] = False,
) -> None:
    """Enrolment against the stopping rule. Names no leader until the rule is met."""
    context = make_context()
    spec, run_state = _experiment(slug)
    settings = context.load_settings()
    admin = context.make_admin(settings)
    env, experiment_id = run_state.environment_key, run_state.experiment_id
    try:
        experiment = admin.get_experiment(env, experiment_id)
        results = ExperimentResults.from_api(admin.get_results(env, experiment_id))
    except FlagsmithAdminError as error:
        raise _fail(f"status: {error}") from error
    analysis, ledger = _analyse(context, settings, spec, run_state, results)
    exposed = ledger_exposures(spec, analysis.records)
    for line in status_lines(analysis, str(experiment.get("status")), exposed, peek):
        typer.echo(line)
    if peek and not analysis.decision.rule_met:
        try:
            viewer = context.open_repo(Path.cwd()).user_name()
        except GitError as error:
            raise _fail(f"status: {error}") from error
        detail = {
            "as_of": analysis.results.as_of.isoformat() if analysis.results.as_of else None,
            "exposures": analysis.exposures,
        }
        ledger.audit("peek", slug, json.dumps(detail, sort_keys=True), viewer)


@app.command()
def report(
    slug: SlugArgument,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Write the prose from the fixed template.")
    ] = False,
) -> None:
    """Refresh and save Flagsmith results, then write reports/<slug>/report.md."""
    context = make_context()
    spec, run_state = _experiment(slug)
    settings = context.load_settings()
    admin = context.make_admin(settings)
    folder = report_dir(slug)
    try:
        saved = refresh_and_save(
            admin,
            run_state.environment_key,
            run_state.experiment_id,
            folder,
            context.now(),
            context.sleep,
        )
    except FlagsmithAdminError as error:
        raise _fail(f"report: {error}") from error
    analysis, _ = _analyse(context, settings, spec, run_state, saved.results)
    drafter = None if no_llm else context.make_drafter(settings)
    written = write_narrative(analysis, drafter, settings.sa_operator_model)
    path = folder / "report.md"
    shipped = folder / DECISION_FILE
    record = shipped.read_text(encoding="utf-8") if shipped.exists() else None
    text = render(analysis, written, saved.path.as_posix(), saved.note, record)
    path.write_text(text, encoding="utf-8", newline="\n")
    typer.echo(f"Results: {saved.path.as_posix()} ({saved.note})")
    typer.echo(f"Prose: {written.author}")
    typer.echo(f"Decision: {analysis.decision.decision.value}")
    typer.echo(f"Report: {path.as_posix()}")


@app.command()
def propose(
    from_slug: Annotated[
        str, typer.Option("--from-slug", help="Experiment whose sessions are the evidence.")
    ] = DEFAULT_SPEC.stem,
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="Failing sessions to include as transcripts.")
    ] = 10,
) -> None:
    """Draft the next experiment from failing sessions. Writes files only; never Flagsmith."""
    context = make_context()
    spec, run_state = _experiment(from_slug)
    settings = context.load_settings()
    ledger = context.open_ledger(settings)
    records = ledger.sessions_since(run_state.started_at)
    transcripts = ledger.tool_calls_for(r.session_id for r in records if r.outcome is not None)
    evidence = build_evidence(spec, records, transcripts, limit, taken_slugs(DEFAULT_SPEC.parent))
    if not evidence.sessions:
        raise _fail(f"propose: {from_slug} has no finished non-correct sessions to learn from")
    model = context.make_proposer(settings)
    if model is None:
        typer.echo(json.dumps(evidence.pack(), indent=2))
        raise _fail(
            "propose: GEMINI_API_KEY is not set, so no proposal was drafted. The evidence "
            "pack is printed above."
        )
    try:
        result, attempts = draft(model, evidence)
    except Exception as error:  # any SDK or network failure; nothing has been written
        code = getattr(error, "code", "")
        raise _fail(f"propose: the model call failed: {type(error).__name__} {code}") from error
    if result is None:
        failed = PROPOSALS / ".failed" / f"{context.now():%Y%m%dT%H%M%SZ}.json"
        failed.parent.mkdir(parents=True, exist_ok=True)
        answers = [{"raw": a.raw, "rejected": a.error} for a in attempts]
        failed.write_text(json.dumps(answers, indent=2) + "\n", encoding="utf-8")
        reasons = "".join(f"\n  attempt {i}: {a.error}" for i, a in enumerate(attempts, 1))
        raise _fail(
            f"propose: the model's answer was rejected twice:{reasons}\n"
            f"Raw answers saved to {failed.as_posix()}"
        )
    slug = result.spec.slug
    spec_file = PROPOSALS / f"{slug}.yaml"
    rationale_file = PROPOSALS / f"{slug}.md"
    rationale = rationale_markdown(
        evidence, result, spec_file.as_posix(), settings.sa_operator_model, context.now()
    )
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(spec_yaml(result.spec, from_slug), encoding="utf-8", newline="\n")
    rationale_file.write_text(rationale, encoding="utf-8", newline="\n")
    detail = {
        "source": from_slug,
        "spec": spec_file.as_posix(),
        "rationale": rationale_file.as_posix(),
        "model": settings.sa_operator_model,
        "attempts": len(attempts),
        "cited_sessions": result.proposal.cited_sessions,
    }
    ledger.audit("propose", slug, json.dumps(detail, sort_keys=True), None)
    if len(attempts) > 1:
        typer.echo(f"First answer rejected ({attempts[0].error}); the retry passed.")
    typer.echo(f"Spec: {spec_file.as_posix()}")
    typer.echo(f"Rationale: {rationale_file.as_posix()}")
    target = DEFAULT_SPEC.parent / f"{slug}.yaml"
    typer.echo(
        f"Next: review and edit both files, move the spec to {target.as_posix()} (where "
        f"`sa status` and `sa report` look for it), add a {slug} entry to HYPOTHESIS.md, "
        f"commit both, then `sa start {target.as_posix()}` (try --dry-run first)."
    )


def _print_ship_plan(plan: ShipPlan) -> None:
    """The rule, the numbers it was applied to, the decision, and the exact changes."""
    analysis = plan.analysis
    rule = analysis.spec.stopping_rule
    typer.echo(
        f"\nResults: {plan.results_file.as_posix()} (Flagsmith as of {analysis.results.as_of})"
    )
    typer.echo(
        f"Rule (HYPOTHESIS.md section 10): ship {analysis.spec.variants[0].key} only if its win "
        f"probability on {analysis.spec.primary_metric.name!r} is at least "
        f"{rule.win_probability_threshold:g} and no guardrail's lift interval lies entirely on "
        "its bad side; otherwise keep control."
    )
    typer.echo("Numbers:")
    for row in analysis.rows:
        stats = ", ".join(
            f"{arm} n={s.n} mean={s.mean:.3f}" if s and s.mean is not None else f"{arm} n/a"
            for arm, s in row.stats.items()
        )
        inference = (
            f"win probability {row.inference.chance_to_win:.3f}, lift {row.inference.lift:.3f} "
            f"[{row.inference.ci_low:.3f}, {row.inference.ci_high:.3f}] ({row.inference_source})"
            if row.inference
            else "no inference"
        )
        typer.echo(f"  {row.metric.role} {row.metric.name}: {stats}; {inference}")
    ledger_counts = guardrail_ledger_counts(analysis)
    for name, arms in ledger_counts.items():
        counts = ", ".join(f"{arm} {c['count']} of {c['scored']}" for arm, c in arms.items())
        typer.echo(f"  ledger counts for {name} (scored sessions): {counts}")
    if ledger_counts:
        trap = ", ".join(r.session_id for r in analysis.trap_wrong) or "none"
        typer.echo(
            f"  {analysis.spec.variants[0].key} wrong bookings on trap scenarios (ledger): "
            f"{len(analysis.trap_wrong)} ({trap})"
        )
    typer.echo("Rule applied:")
    for reason in analysis.decision.reasons:
        typer.echo(f"  - {reason}")
    typer.echo(f"Decision: {plan.decision.value}")
    for warning in plan.warnings:
        typer.echo(f"Warning: {warning}.")
    typer.echo("Changes in Flagsmith:")
    for change in plan.changes():
        typer.echo(f"  - {change}")


@app.command()
def ship(
    slug: SlugArgument,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the decision and the changes, then exit.")
    ] = False,
) -> None:
    """Apply the decision rule; on typed confirmation end the experiment and roll it out."""
    context = make_context()
    spec, run_state = _experiment(slug)
    settings = context.load_settings()
    admin = context.make_admin(settings)
    folder = report_dir(slug)
    results_file = latest_saved(folder)
    if results_file is None:
        raise _fail(f"ship: no saved results in {folder.as_posix()}; run `sa report {slug}` first")
    raw = json.loads(results_file.read_text(encoding="utf-8"))
    analysis, ledger = _analyse(context, settings, spec, run_state, ExperimentResults.from_api(raw))
    try:
        experiment = admin.get_experiment(run_state.environment_key, run_state.experiment_id)
        plan = plan_ship(analysis, experiment, results_file)
        done = plan.record_only and already_verified(folder, ledger.has_audit("ship_verify", slug))
        if done:
            raise ShipRefusedError(
                f"experiment {slug} has already been shipped and recorded: {done}"
            )
    except ShipRefusedError as error:
        for reason in analysis.decision.reasons:
            typer.echo(f"  - {reason}")
        raise _fail(f"ship: refused: {error}; nothing was changed") from error
    except FlagsmithAdminError as error:
        raise _fail(f"ship: {error}") from error
    _print_ship_plan(plan)
    if settings.sa_server_url is None:
        raise _fail("ship: SA_SERVER_URL is not set, so the served style cannot be checked")
    if settings.sa_lab_key is None:
        raise _fail(
            "ship: SA_LAB_KEY is not set, so the smoke session would count as field traffic"
        )
    if dry_run:
        typer.echo("\nDry run: nothing was changed.")
        return
    try:
        approver = context.open_repo(Path.cwd()).user_name()
    except GitError as error:
        raise _fail(f"ship: {error}") from error
    phrase = f"ship {slug}"
    action = (
        "record this decision for the experiment that has already ended"
        if plan.record_only
        else "end the experiment and apply this decision"
    )
    typed = typer.prompt(f"\nType `{phrase}` to {action}")
    if typed.strip() != phrase:
        raise _fail("ship: confirmation did not match; nothing was changed")
    earlier: str | None = None
    if plan.record_only:
        outcome = Outcome(dict(experiment))
        earlier = pending_record(folder)  # replaced in the report, not kept beside the new one
    else:
        check = (
            f"Check experiment {run_state.experiment_id} in Flagsmith before running `sa ship` "
            "again: if it ended with this rollout, the re-run records the decision without "
            "ending it."
        )
        try:
            outcome = complete(plan, admin)
        except FlagsmithAdminError as error:
            saved = _record_attempt(ledger, plan, approver, error)
            raise _fail(f"ship: the complete/ call failed: {error}\n{saved} {check}") from error
        except KeyboardInterrupt as error:  # the call may already have reached Flagsmith
            saved = _record_attempt(ledger, plan, approver, error)
            raise _fail(f"ship: interrupted during the complete/ call.\n{saved} {check}") from error
    # The experiment has ended: record that now, so no later failure can leave it unrecorded.
    now = context.now()
    pending = _record(ledger, folder, plan, outcome, approver, now, earlier)
    try:
        read_back(plan, admin, outcome)
        _smoke(context, settings, plan, outcome)
    except KeyboardInterrupt:
        outcome.problems.append("interrupted before verification finished")
    outcome.checked = True
    _record(ledger, folder, plan, outcome, approver, now, pending)
    typer.echo(
        f"\nExperiment {slug} is {outcome.experiment.get('status')}; flag serves "
        f"{outcome.served!r} (expected {plan.serve_arm}). Approved by {approver}. Recorded in "
        f"the audit log and {(folder / DECISION_FILE).as_posix()}."
    )
    if outcome.problems:
        raise _fail(
            "ship: verification failed:\n" + "\n".join(f"  - {p}" for p in outcome.problems)
        )


def _record_attempt(ledger: Ledger, plan: ShipPlan, approver: str, error: BaseException) -> str:
    """Audit a `complete/` call that failed client-side or was interrupted; returns a sentence.

    The CLI failing does not prove Flagsmith refused the change, so the attempt is kept.
    """
    detail = {
        "decision": plan.decision.value,
        "rollout_decision": plan.rollout_decision,
        "rollout_variant": plan.rollout_variant,
        "results_file": plan.results_file.as_posix(),
        "error": str(error) or type(error).__name__,
    }
    try:
        ledger.audit(
            "ship_attempt", plan.analysis.spec.slug, json.dumps(detail, sort_keys=True), approver
        )
    except Exception as audit_error:  # report both failures; the first one is the cause
        return f"The attempt could not be audited either ({type(audit_error).__name__})."
    return "The attempt is in the audit log (ship_attempt); no decision was recorded."


def _record(
    ledger: Ledger,
    folder: Path,
    plan: ShipPlan,
    outcome: Outcome,
    approver: str,
    now: datetime,
    replacing: str | None,
) -> str:
    """Write the audit row and the decision record for the outcome so far; returns the record.

    The first call (`ship`) follows the irreversible change; the second (`ship_verify`) adds
    what was verified and replaces the first record in the report.
    """
    action = "ship_verify" if outcome.checked else "ship"
    detail = json.dumps(audit_detail(plan, outcome), sort_keys=True)
    try:
        ledger.audit(action, plan.analysis.spec.slug, detail, approver)
    except Exception as error:  # the change is made; the file record below must still be written
        outcome.problems.append(f"audit log write ({action}) failed: {type(error).__name__}")
    record = decision_record(plan, outcome, approver, now)
    try:
        append_record(folder, record, replacing)
    except OSError as error:  # the change is made; the audit row and the next write remain
        outcome.problems.append(
            f"decision record write ({DECISION_FILE}) failed: {type(error).__name__}: {error}"
        )
    return record


def _smoke(context: Context, settings: Settings, plan: ShipPlan, outcome: Outcome) -> None:
    """Run smoke sessions until the server serves the expected style; note any mismatch.

    Skipped unless the read-back shows the experiment ended, so no smoke session can be
    enrolled in it.
    """
    if not outcome.smoke_safe:
        outcome.problems.append(
            "smoke session skipped: the experiment was not read back as completed"
        )
        return
    expected = ErrorStyle.from_flag_value(plan.serve_value)
    try:
        outcome.observed = smoke_until(lambda: context.smoke(settings), expected, context.sleep)
    except Exception as error:  # any network or protocol failure: the change is already made
        outcome.problems.append(f"smoke session failed: {type(error).__name__}: {error}")
        return
    typer.echo(f"Smoke session: served style {outcome.observed.model_dump()}")
    if outcome.observed != expected:
        outcome.problems.append(
            f"smoke session saw {outcome.observed.model_dump()}, expected {expected.model_dump()}"
        )
