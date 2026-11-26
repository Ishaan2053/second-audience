# SA-09: Operator: spec, doctor, start, and going live (Checkpoint B)

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 2.5h |
| Depends on | SA-07, SA-08 |
| Components | `operator/cli.py`, `operator/spec.py`, `operator/flagsmith_admin.py`, `experiments/`, `HYPOTHESIS.md` |
| Branch | `feat/SA-09-operator-start` |
| Can run in parallel with | None |

## 1. Objective

Build the operator agent's foundation (`sa doctor`, `sa start`), pre-register the hypothesis, and start the production experiment with lab sessions flowing.

## 2. Context

Track 02 asks for an agent or tool that operates Flagsmith through its CLI or API, saves real time, keeps a human in the approval path and gives clear output. `sa start` turns a reviewed spec file into a running experiment after typed confirmation. Track 01 asks for a clear hypothesis: `HYPOTHESIS.md` must be committed before the experiment starts so that git proves it. After this ticket the scenarios, heuristic, system prompt and model list are frozen.

## 3. Scope

**In scope**

- Typer app `sa` with `doctor` and `start`; console script entry.
- `ExperimentSpec` model and `experiments/error-style-v1.yaml`.
- `FlagsmithAdmin` wrapper over the `flagsmith` CLI and `flagsmith api`.
- `HYPOTHESIS.md`.
- Checkpoint B: production experiment started; `v0.1.0` tagged.

**Out of scope**

- `status`, `report` (SA-12), `propose` (SA-13), `ship` (SA-14).
- Any direct HTTP calls with an admin key stored in this project.

## 4. Implementation instructions

- **`FlagsmithAdmin`**: runs the CLI with `subprocess.run([...], capture_output=True, text=True)`, never `shell=True`. Methods: `whoami`/login check, `warehouse_connected(env)`, `get_feature(name)`, `create_feature(name, value)`, `add_variant(name, key, value)`, `create_metric(env, name, event, aggregation, direction)`, `create_experiment(env, payload)`, `start_experiment(env, id)`, `refresh_results(env, id)`, `get_results(env, id)`. Use the exact commands and payloads recorded in `docs/spike-findings.md`. Raise a typed `FlagsmithAdminError` carrying stderr on failure. All methods are idempotent where possible: look up before creating.
- **Spec format** (`experiments/<slug>.yaml`):
  ```yaml
  slug: error-style-v1
  environment: production
  flag: error_style
  hypothesis: >
    One sentence.
  control:
    key: control
    value: {suggest_alternative: false, corrected_call: false, caution_note: false}
  variants:
    - key: prescriptive
      value: {suggest_alternative: true, corrected_call: true, caution_note: false}
  metrics:
    - {name: Task completed correctly, event: task_completed, direction: up, primary: true}
    - {name: Wrong booking, event: task_wrong_booking, direction: down, primary: false}
  allocation: {traffic_percent: 100, split: {control: 50, prescriptive: 50}}
  stopping_rule:
    min_exposed_per_arm: 75
    win_probability_threshold: 0.95
    hard_end: "2026-11-29T12:00:00+05:30"
  ```
  Validate with pydantic: split sums to 100, exactly one primary metric, variant values parse as `ErrorStyle`, events are known names.
- **`sa doctor`**: prints a pass/fail table for settings, CLI installed and logged in, warehouse connected for the spec's environment, database reachable, model key present, server reachable at `SA_SERVER_URL`. Exit code 1 on any failure.
- **`sa start <spec>`**:
  1. Run the doctor checks. Refuse to continue on failure.
  2. Refuse if the working tree has uncommitted changes to the spec or `HYPOTHESIS.md`, or if `HYPOTHESIS.md` does not mention the spec's slug.
  3. Print the plan: what will be created, in which environment, and the stopping rule.
  4. Require the user to type the slug to confirm. Anything else aborts with no changes.
  5. Create or reuse the flag and variant, the metrics, the experiment; start it; verify status is running.
  6. Write `experiments/.state/<slug>.json` (IDs, start time, git commit hash) and an `audit_log` row with `approved_by` from `git config user.name`.
  `--dry-run` prints the plan and exits.
- **`HYPOTHESIS.md`** (the agent drafts it; the developer edits and approves the wording before commit). Sections: question; hypothesis and direction; arms with example payloads; primary and guardrail metrics; population (models, lab versus field); scenario set and the aligned/trap mix; the suggestion heuristic; the fixed system prompt; exposure rule; stopping rule and decision rule; planned breakdowns, marked exploratory; what is frozen and at which commit; known limitations.
- **Decision rule to pre-register:** ship the variant only if its win probability on `task_completed` is at least the threshold and `task_wrong_booking` is not credibly worse; otherwise keep control and report the result as it stands.
- The developer confirms the stopping-rule numbers using the pilot's exposure rate and the quota estimate from SA-08 before committing.

**Checkpoint B procedure:**

1. Merge this ticket. Commit `HYPOTHESIS.md` and the spec on `main`.
2. Remove any temporary `error_style` flag from `production` if one exists.
3. `sa start experiments/error-style-v1.yaml` against `production`.
4. Point the server at `production`, start the lab runner with the full plan.
5. After the first 20 sessions, confirm exposures in Flagsmith match the ledger.
6. Tag `v0.1.0` with release notes recording the freeze (commit hash, scenarios, heuristic, prompt, models).

## 5. Dependencies

- Tickets: SA-07, SA-08
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `sa doctor` reports each check and exits non-zero when one fails.
- `sa start --dry-run` prints the full plan and changes nothing.
- `sa start` aborts without changes when the confirmation text is wrong.
- `sa start` is safe to run twice: the second run reuses existing objects and reports that the experiment is already running.
- `HYPOTHESIS.md` is committed on `main` before the experiment's recorded start time.
- Checkpoint B: the production experiment is running, lab sessions are flowing, early counts reconcile, and `v0.1.0` exists.

## 7. Testing requirements

- Unit tests for spec validation and for `start` with `FlagsmithAdmin` faked: confirmation, dry run, idempotence, dirty-tree refusal.
- Unit tests for `FlagsmithAdmin` command construction with `subprocess.run` patched.
- Manual: `sa start` against `development` first, then Checkpoint B on `production`.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-09: Operator: spec, doctor, start, and going live (Checkpoint B)`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-09-operator-start`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-09: Operator: spec, doctor, start, and going live (Checkpoint B)` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-10 and SA-12 can both begin immediately and are independent. Recommended order: SA-10, SA-11, then SA-12, so the public endpoint collects field sessions for longer.
