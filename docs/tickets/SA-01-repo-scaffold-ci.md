# SA-01: Repository scaffold, tooling and CI

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | infrastructure |
| Estimate | 1h |
| Depends on | None |
| Components | Repository, CI, configuration |
| Branch | `chore/SA-01-repo-scaffold-ci` |
| Can run in parallel with | SA-02 (the developer's account steps) |

## 1. Objective

Create the public repository with the Python project skeleton, quality tooling, validated configuration and a green CI pipeline, so every later ticket lands on a working base.

## 2. Context

Nothing exists yet. The hackathon requires that work is built inside the event window, so the first commit must be on or after Nov 26, 2026. The planning documents (`CLAUDE.md`, `AGENTS.md`, `docs/`) are supplied by the developer and must be committed as part of this ticket.

## 3. Scope

**In scope**

- Public GitHub repository `second-audience` under the developer's account, MIT licence.
- `uv` project, Python 3.12+, `src/second_audience/` layout with empty subpackages `core`, `booking`, `lab`, `operator`.
- `ruff` (lint and format), `mypy` (strict on `src`), `pytest` with a `network` marker.
- `second_audience.config.Settings` using pydantic-settings, with every variable from `docs/02-architecture.md` section 3.1.
- `.env.example`, `.gitignore`, issue and pull request templates, labels.
- GitHub Actions workflow as described in `docs/06-cicd-release.md` (Docker job is added later in SA-11).
- `docs/tickets/STATUS.md` committed and updated.

**Out of scope**

- Any feature code.
- Deployment.
- Branch protection rules that would block a solo developer from merging.

## 4. Implementation instructions

- Create the repo with `gh repo create second-audience --public --license mit` (confirm the name is free first).
- `uv init --package`, then add runtime dependencies only as later tickets need them. This ticket adds: `pydantic`, `pydantic-settings`. Dev group: `ruff`, `mypy`, `pytest`, `pytest-asyncio`.
- `Settings` contract:
  - Required in `prod`: `FLAGSMITH_ENVIRONMENT_KEY`, `SA_DATABASE_URL`, `SA_LAB_KEY`.
  - Optional everywhere: `GEMINI_API_KEY`, `FLAGSMITH_API_URL`, `SA_SERVER_URL`.
  - Defaults: `SA_ENV=dev`, `SA_FLAG_NAME=error_style`, `SA_DATABASE_URL=sqlite:///./sa.db`, `SA_MAX_TOOL_CALLS=12`, `SA_SESSION_IDLE_SECONDS=600`, `SA_FIELD_SESSIONS_PER_IP_PER_HOUR=10`.
  - `SA_LAB_MODELS` parses a comma-separated string into a list.
  - Secrets use `SecretStr` so they never print in logs or reprs.
  - Expose `get_settings()` cached with `functools.lru_cache`.
- Register the console script `sa = second_audience.operator.cli:app` only when SA-09 creates it; do not add a stub now.
- CI: use `astral-sh/setup-uv`, cache enabled. Jobs per `docs/06-cicd-release.md`. Use Context7 to confirm current action versions.
- Create labels with `gh label create`: `feature`, `infrastructure`, `documentation`, `testing`, `P0`, `P1`, `P2`, `P3`.
- Write a placeholder `README.md` (name, one-line pitch, "work in progress"). The full README is SA-16.

## 5. Dependencies

- Tickets: None
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `uv sync` then `uv run pytest` passes locally on Windows PowerShell.
- `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy src` pass.
- CI is green on the pull request and on `main` after merge.
- Instantiating `Settings` with `SA_ENV=prod` and a missing required variable raises a validation error naming the variable.
- `repr(settings)` does not reveal secret values.
- The planning documents are present in the repository.

## 7. Testing requirements

- Unit tests for `Settings`: defaults, required-in-prod validation, model list parsing, secret redaction.
- Manual: open the Actions run and confirm each job ran.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-01: Repository scaffold, tooling and CI`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `chore/SA-01-repo-scaffold-ci`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-01: Repository scaffold, tooling and CI` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-02 if the developer has finished the Flagsmith account steps, otherwise SA-03. SA-03 can start immediately; it does not depend on SA-02.
