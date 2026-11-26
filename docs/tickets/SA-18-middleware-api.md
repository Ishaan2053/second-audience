# SA-18: Stretch: public middleware API and a second example server

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P2 |
| Type | feature |
| Estimate | 2h |
| Depends on | SA-07. Do not start before SA-12 is merged. |
| Components | `core/__init__.py`, `core/middleware.py`, `examples/` |
| Branch | `feat/SA-18-middleware-api` |
| Can run in parallel with | SA-13, SA-14, SA-15 |

## 1. Objective

Show that Second Audience is a layer for any MCP server, not just the booking server, by exposing a small public API in `second_audience.core` and using it in a minimal second example.

## 2. Context

The project's value claim is that server owners can test their own agent-facing responses. Until now the experiment logic is wired by hand inside the booking server. A small, documented API plus a second, unrelated example makes the claim concrete and strengthens the Impact criterion. Build only if the schedule is ahead: it is first in the cut order.

## 3. Scope

**In scope**

- A public helper for variant-dependent responses and outcome tracking.
- `examples/inventory_server/`: a tiny second MCP server using it.
- README section update.

**Out of scope**

- Refactoring the booking server onto the new API (the experiment is frozen).
- PyPI publishing.
- A second running experiment.

## 4. Implementation instructions

- Design the smallest API that covers the pattern already proven in the booking server. A suggested shape, to be adjusted to what reads best in the example:
  ```python
  sa = SecondAudience.from_settings()

  style = sa.variant(session_id, parse=MyStyle.from_flag_value)  # lazy exposure
  sa.outcome(session_id, "task_completed")
  ```
  It composes `ExperimentClient` and `Ledger`; it adds no new behaviour.
- The example server has two or three tools in a different domain (for example stock reservation with an "insufficient stock" state error) and one style setting. It needs no scenarios or lab runs; a short scripted client demonstrates both variants.
- The example runs with `NullExperimentClient` by default so it works with no Flagsmith account, and against Flagsmith when a key is set.
- Do not import from `second_audience.booking` in `core` or in the example.
- Update the README's "Use it on your own MCP server" section with the real, tested snippet.

## 5. Dependencies

- Tickets: SA-07. Do not start before SA-12 is merged.
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- The example server runs and its scripted client shows two different responses for the same failing call under two styles.
- `second_audience.core` has no dependency on `second_audience.booking` (enforce with a test that inspects imports).
- The README snippet is copied from tested code.
- No frozen experiment file changed.

## 7. Testing requirements

- Unit tests for the helper with `NullExperimentClient`.
- An integration test running the example's scripted client.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-18: Stretch: public middleware API and a second example server`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P2` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-18-middleware-api`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-18: Stretch: public middleware API and a second example server` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

Return to the main sequence at the next unfinished ticket.
