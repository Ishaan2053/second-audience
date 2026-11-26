# SA-11: Public deployment

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | infrastructure |
| Estimate | 2h |
| Depends on | SA-10, SA-05 (hosted Postgres) |
| Components | `Dockerfile`, host configuration, CI, `docs/deployment.md` |
| Branch | `chore/SA-11-public-deploy` |
| Can run in parallel with | SA-12, SA-13 |

## 1. Objective

Deploy the server to a free host at a public HTTPS URL, connected to the production Flagsmith environment and the shared ledger, and verify it with an external MCP client.

## 2. Context

The demo and README invite anyone to connect an agent. The host must cost nothing, run a long-lived Python process, and serve streamable HTTP. Free hosts sleep and cold-start, and their disks are ephemeral, which is why the ledger is in hosted Postgres and why the experiment client falls back to the control style when Flagsmith is slow.

## 3. Scope

**In scope**

- Choose the host using the criteria below and record the decision.
- `Dockerfile` and `.dockerignore`; Docker build job in CI.
- Deployment from `main`, environment configured on the host.
- External verification and `docs/deployment.md`.
- Tag `v0.2.0`.

**Out of scope**

- Custom domains.
- Autoscaling or more than one instance (sessions are in memory, so exactly one instance).
- Paid plans of any kind.

## 4. Implementation instructions

- **Host selection.** Check current free-tier terms with web search and Context7; do not rely on memory. Candidates to compare: Render free web service, Hugging Face Spaces (Docker), Koyeb free instance. Criteria, in order: no payment card required; supports a long-lived HTTP server with streaming responses; a single always-same instance; sleep and cold-start behaviour; request timeout long enough for MCP; deploys from GitHub. Record the comparison and choice in `docs/deployment.md`. If none qualifies, stop and tell the developer.
- **Dockerfile:** slim Python base, install with `uv sync --frozen --no-dev`, run as a non-root user, `SA_GIT_COMMIT` passed as a build argument, start the server on `0.0.0.0:$PORT` with HTTP transport. One process, one worker.
- **Host environment:** `SA_ENV=prod`, `FLAGSMITH_ENVIRONMENT_KEY` (production), `SA_DATABASE_URL` (the shared Postgres), `SA_LAB_KEY`, `SA_TRUST_PROXY=true`, `SA_FIELD_ENABLED=true`. No model key and no Flagsmith admin credential on the host.
- **Lab runs stay local** by default, against the developer's local server, writing to the same Postgres and the same Flagsmith environment. Do not redirect the running lab plan to the public host.
- **Cold starts:** document the expected first-response delay. Optionally add a scheduled GitHub Actions workflow that requests `/healthz` every 10 minutes to reduce sleeping, if the host's terms allow it.
- **CI:** add a `docker build` job. Do not push images from CI unless the chosen host requires it.
- **Verification from outside:** connect a real MCP client (Claude Code or another client that supports remote HTTP servers) to `https://<host>/mcp`, give it the suggested instruction, and let it complete a task. Confirm a `field` session with an outcome in the ledger and, if it hit a styled error, a variant.
- `docs/deployment.md`: the decision, step-by-step redeploy instructions, environment variables, how to disable field sessions quickly, how to read logs, and connection snippets for at least two MCP clients (confirm each client's current configuration format with Context7).

## 5. Dependencies

- Tickets: SA-10, SA-05 (hosted Postgres)
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `https://<host>/healthz` returns status and the deployed commit hash.
- An external MCP client completed a task and the session is in the ledger with `source = field`.
- The host holds no model key and no Flagsmith admin credential.
- CI builds the Docker image on every pull request.
- `docs/deployment.md` lets someone redeploy without asking questions.
- `v0.2.0` tagged with release notes including the public URL.

## 7. Testing requirements

- `docker build` and `docker run` locally, then the SA-07 smoke script against the container, pointed at the `development` environment.
- Manual external verification as described. Report the actual client used and what happened.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-11: Public deployment`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `chore/SA-11-public-deploy`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-11: Public deployment` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-12. If SA-12 is already done, SA-13.
