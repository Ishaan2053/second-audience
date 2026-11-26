# SA-20: Add-on: operational controls as Flagsmith flags

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P2 |
| Type | feature |
| Estimate | 1h |
| Depends on | SA-10, SA-11 |
| Components | `core/controls.py`, `booking/limits.py` |
| Branch | `feat/SA-20-flagsmith-controls` |
| Can run in parallel with | SA-13, SA-14, SA-15 |

## 1. Objective

Move the public endpoint's kill switch and field limits from environment variables to Flagsmith feature flags and remote config, so they can be changed instantly without a redeploy.

## 2. Context

Today `SA_FIELD_ENABLED` and the rate limits are environment variables, so changing them on the free host means a restart, which drops in-memory sessions. Flagsmith's basic feature flags and remote config solve exactly this, and using them shows Flagsmith doing operational work in the project in addition to experimentation. The experiment flag `error_style` is not touched.

## 3. Scope

**In scope**

- Flags in `production` and `development`: `field_enabled` (boolean), `field_limits` (remote config JSON with sessions per IP per hour and max concurrent sessions).
- `Controls` reader with a short cache and environment-variable fallback.
- `/healthz` shows the current control values and their source.

**Out of scope**

- Any change to `error_style` or to frozen experiment files.
- Per-identity evaluation of control flags.

## 4. Implementation instructions

- Create the flags with the `flagsmith` CLI. Record the commands in `docs/deployment.md`.
- `Controls.get()` reads environment-level flags (no identity) through the SDK, caches the result for 60 seconds, and falls back to the `Settings` values if Flagsmith is unreachable or a value is malformed. Validate `field_limits` with pydantic.
- Keep the cache: flag requests count against the free plan's 50,000 requests per month. State the expected request volume in the pull request.
- `limits.py` reads from `Controls` instead of `Settings`. Behaviour is otherwise unchanged.
- Document the "turn off field sessions now" procedure as one CLI command.

## 5. Dependencies

- Tickets: SA-10, SA-11
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- Toggling `field_enabled` off in Flagsmith refuses new field sessions within about a minute, with no restart; lab sessions continue.
- Changing `field_limits` takes effect without a restart.
- With Flagsmith unreachable, the environment-variable values apply.
- No frozen experiment file changed.

## 7. Testing requirements

- Unit tests for cache expiry, fallback and malformed config.
- Manual: toggle in `development` and observe.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-20: Add-on: operational controls as Flagsmith flags`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P2` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-20-flagsmith-controls`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-20: Add-on: operational controls as Flagsmith flags` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

Return to the main sequence.
