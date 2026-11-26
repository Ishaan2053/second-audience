# Second Audience: agent instructions

Read this file fully before every ticket. It is the standing context for the project.
`AGENTS.md` is a copy of this file; keep them identical.

## What this project is

Second Audience is A/B testing for the interfaces AI agents use. A product's website is
tested on humans constantly; the MCP server the same product exposes to agents is worded
by taste and never measured. Second Audience measures it, with agents as the experiment
subjects and Flagsmith Experimentation as the engine.

It is an entry for **Toggle Wars: The Flagsmith Experimentation Hackathon**
(Nov 26, 8:00 AM IST to Nov 29, 8:00 PM IST, 2026), entered in Track 01 (Test Something
Real) and Track 02 (AI Agents and Feature Flags). Solo developer, part-time, 20-28 hours.

Three parts:

1. **Core middleware** (`second_audience.core`): assigns a variant per agent session from
   a Flagsmith multivariate flag, records exposure and conversion events, and keeps a
   local ledger of every session.
2. **Reference server** (`second_audience.booking`): a room-booking MCP server. It is the
   test subject, not the product.
3. **Operator agent** (`second_audience.operator`, CLI command `sa`): proposes, starts,
   reports on and ships experiments through the Flagsmith CLI and Admin API, with explicit
   human approval before anything changes.

Plus a **lab runner** (`second_audience.lab`) that drives agent sessions on Gemini
free-tier models, and a **public endpoint** where anyone can connect their own agent.

## Settled decisions (do not reopen)

- Python 3.12+, `uv`, `ruff`, `mypy`, `pytest`. FastMCP for the server. Flagsmith Python
  SDK for flags and events. Typer for the CLI. SQLAlchemy Core for the ledger.
- Flagsmith Cloud free plan. Flags, metrics and experiments are managed through the
  `flagsmith` CLI (v2+) and `flagsmith api <path>`. Do not use the Flagsmith MCP server:
  it cannot start experiments.
- The one experiment: on state-dependent failures, compare an **informative** error (states
  the fact) with a **prescriptive** error (adds a suggested alternative and a corrected
  call). Both arms use the same strict tool schema.
- Primary metric: tool calls per session (`task_finished`, mean of its value). Guardrails:
  `task_completed` (the final server state satisfies the task's constraints) and
  `task_wrong_booking`. Some scenarios are traps where the suggestion violates the user's
  request. Changed from `task_completed` in SA-09, before the start, because the pilot put
  completion at its ceiling in both arms (`HYPOTHESIS.md`).
- Exposure is recorded lazily, at the first styled error a session receives.
- Lab traffic comes from Gemini free-tier models only. A second provider is a contingency
  (ticket SA-19), used only if quotas force it.
- Lab and field sessions are tagged separately and always labelled honestly in reports.
- No web dashboard. Results are shown on Flagsmith's results page and in generated
  Markdown reports.
- Nothing related to Konnect appears anywhere in this repository.

## Non-goals

Human-persona simulation, a custom dashboard, automatic variant-rewriting loops, more than
one running experiment, multi-tenant accounts, authentication for the public endpoint
beyond rate limiting, PyPI publishing.

## Engineering conventions

- DRY. Typed code; `mypy` must pass. Public functions have type hints and a one-line docstring.
- All configuration goes through `second_audience.config.Settings` (pydantic-settings).
  Never read `os.environ` elsewhere. Never commit secrets; `.env` is git-ignored and
  `.env.example` lists every variable.
- Use the newest compatible version of every library. When an API detail is uncertain
  (Flagsmith SDK, FastMCP, MCP client SDK, Gemini SDK, hosting platform), look it up with
  the **Context7 MCP** before writing code. Do not guess from memory. Record anything
  surprising in `docs/spike-findings.md`.
- Subprocess calls use argument lists, never `shell=True`. The developer works on Windows
  PowerShell: every documented command must work there (`uv run ...`).
- LLM output never supplies numbers in reports. Numbers come from Flagsmith results or the
  ledger; the LLM only writes prose around them.
- No dead code, no unexplained workarounds, no dependency without a reason.

## GitHub workflow (lean)

1. Each ticket is a GitHub issue titled `SA-XX: <title>`. If it does not exist, create it
   with `gh issue create` and paste the ticket file as the body.
2. Branch from `main`: `<type>/SA-XX-<slug>` (the ticket names it). Never commit to `main`.
3. One pull request per ticket. Title `SA-XX: <title>`. Body: summary, why, `Closes #<issue>`,
   key decisions, tests run with real results, known limitations. Add labels.
4. CI must be green. Squash-merge. Delete the branch.
5. `/code-review` and `/ship` are mandatory in SA-17 before the final release. Run
   `/code-review` earlier only if a ticket says so.

## Execution protocol for every ticket

1. Read the ticket file in `docs/tickets/` and this file.
2. Check `docs/tickets/STATUS.md` to confirm dependencies are merged.
3. Inspect the current repository state before changing anything.
4. Confirm or create the issue, then create the branch.
5. Implement only the ticket's scope. If the plan needs to change, stop and explain why
   before making consequential changes.
6. Write and run the tests the ticket requires. Run `uv run ruff check .`,
   `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`.
7. Update documentation the ticket touches.
8. Commit with meaningful messages, open the PR, verify CI, merge when green.
9. Update `docs/tickets/STATUS.md` and write the handoff.

Never state that a test, build, deployment, PR, merge, release or review succeeded unless
you ran it and saw the result. If blocked: explain the blocker, its root cause, the smallest
viable resolution, and ask only when a decision or permission is needed.

## Handoff format (end of every ticket)

- Ticket ID and title
- What was implemented
- Important files changed
- Tests executed and actual results
- Build and CI status
- Code-review findings and resolution (if run)
- Issue and PR references, merge status
- Outstanding limitations
- Next recommended ticket

## Where things are

- `docs/01-master-plan.md` scope, priorities, demo plan
- `docs/02-architecture.md` components, contracts, data model
- `docs/03-constraint-matrix.md` hackathon rules and judging alignment
- `docs/04-roadmap.md` phases and milestones
- `docs/05-risk-register.md`
- `docs/06-cicd-release.md`
- `docs/07-submission-checklist.md`
- `docs/tickets/` backlog, execution order, status
