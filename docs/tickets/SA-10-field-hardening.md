# SA-10: Field hardening for the public endpoint

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-07 |
| Components | `booking/server.py`, `booking/limits.py`, landing page |
| Branch | `feat/SA-10-field-hardening` |
| Can run in parallel with | SA-12, SA-13 |

## 1. Objective

Make the server safe and understandable to expose publicly: rate limits, concurrency caps, a landing page that says what is logged, and a health endpoint.

## 2. Context

Field mode is a core feature: anyone, including a judge, can connect their own MCP client and become an experiment subject. The endpoint has no authentication, so abuse protection is by limits. The experiment is already running when this ticket is built, so nothing here may change tool schemas, error payloads, scenarios or scoring.

## 3. Scope

**In scope**

- Per-IP limit on new field sessions per hour; global cap on concurrent sessions.
- Salted IP hashing for the ledger.
- `GET /` landing page and `GET /healthz`.
- A kill switch setting that disables new field sessions.

**Out of scope**

- Authentication, accounts or API keys for field users.
- Any change to experiment behaviour.
- Deployment (SA-11).

## 4. Implementation instructions

- **Experiment freeze.** Do not modify `world.py`, `scenarios.py`, `checker.py`, `errors.py`, `suggestions.py`, tool schemas or descriptions. If a change there seems necessary, stop and raise it.
- `limits.py`: an in-memory sliding-window limiter keyed by IP hash, and a concurrent-session counter. Limits apply in `begin_task` only, and only to `field` sessions; lab sessions (valid `X-SA-Lab-Key`) are exempt.
- When a limit is hit, `begin_task` returns a plain `RATE_LIMITED` or `SERVER_BUSY` error with a retry hint. These sessions are not created and not recorded as experiment sessions.
- Client IP: read the forwarded-for header only when a `SA_TRUST_PROXY` setting is true (it will be on the host); otherwise use the socket address. Hash with a salt derived from `SA_LAB_KEY` using SHA-256, truncated. Never log or store the raw IP.
- New settings: `SA_TRUST_PROXY` (default false), `SA_FIELD_ENABLED` (default true), `SA_MAX_CONCURRENT_SESSIONS` (default 50), `SA_GIT_COMMIT` (set by the host or Docker build).
- `GET /healthz` returns JSON: status, git commit, active session count, whether Flagsmith and the database were reachable at last check. It must not make a network call on every request; cache the checks briefly.
- `GET /` returns a small static HTML page: what Second Audience is, that this is a research sandbox with fictional data, exactly what is logged (tool calls, a salted IP hash, client name; no personal data), that sessions are part of an A/B experiment on server responses, how to connect, a suggested instruction to give an agent, and a link to the repository. Keep it to one self-contained file with no external assets.
- Suggested instruction shown on the page and later in the README: "Use the second-audience tools. Call begin_task, complete the task it gives you, then call finish_task."
- Use Context7 to confirm how the installed FastMCP version mounts custom HTTP routes alongside the MCP endpoint.
- Update `.env.example` and the environment reference.

## 5. Dependencies

- Tickets: SA-07
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- The eleventh field `begin_task` from one IP within an hour is refused; a lab session from the same IP is accepted.
- With `SA_FIELD_ENABLED=false`, field `begin_task` is refused and lab sessions work.
- No raw IP appears in the database or logs during a test run.
- `/healthz` and `/` respond without authentication; `/healthz` shows the commit hash.
- `git diff` for this pull request touches none of the frozen files.

## 7. Testing requirements

- Unit tests for the limiter (window expiry, exemption, concurrency cap) and IP hashing.
- Integration test over HTTP for the limit and the kill switch.
- Manual: open `/` in a browser and read it as a first-time visitor.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-10: Field hardening for the public endpoint`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-10-field-hardening`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-10: Field hardening for the public endpoint` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-11. It can begin immediately.
