# SA-07: MCP server, session lifecycle and Checkpoint A

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 2h |
| Depends on | SA-03, SA-04, SA-05, SA-06 |
| Components | `booking/sessions.py`, `booking/server.py`, `scripts/smoke.py` |
| Branch | `feat/SA-07-mcp-server` |
| Can run in parallel with | None |

## 1. Objective

Expose the booking domain as a FastMCP server with a strict schema, wire in sessions, budgets, the ledger and the experiment client, and prove the whole pipeline with a scripted client (Checkpoint A).

## 2. Context

This is the first end-to-end vertical slice. After it, a client can begin a task, hit a styled error, be assigned a variant, finish, and have the outcome recorded in the ledger and sent to Flagsmith. No model is involved yet: the smoke script plays fixed moves so that any failure is in our plumbing, not in model behaviour.

## 3. Scope

**In scope**

- `SessionManager`: create, look up, budget, finalise, idle expiry.
- FastMCP app with the seven tools in `docs/02-architecture.md` section 3.2, served over streamable HTTP at `/mcp` and over stdio for local use.
- Lab identification through request headers.
- `scripts/smoke.py` and the Checkpoint A run.

**Out of scope**

- Rate limiting and the landing page (SA-10).
- Deployment (SA-11).
- Any model calls (SA-08).

## 4. Implementation instructions

- Use Context7 for the current FastMCP API: tool registration, typed parameters, HTTP transport, lifespan hooks, reading request headers inside a tool, and mounting extra HTTP routes (needed in SA-10).
- **Strict schema.** `room_id` is an enum of the six IDs. `date` is restricted to the fixed week. `start` and `end` match `HH:MM` on 30-minute steps. `attendees` is an integer from 1 to 20. Schema violations return the framework's validation error, identical in both arms.
- **Tool descriptions** are plain and identical in both arms. They are not under test in experiment 1.
- **`begin_task(scenario_id: str | None)`**: picks the scenario (random when omitted), creates the world from its seed, creates the session, writes the ledger row, and returns `{session_id, brief, max_tool_calls, note}`. The note says this is a research sandbox with fictional data and that calls are logged. It does not mention variants.
- **Source and agent label.** A request carrying header `X-SA-Lab-Key` equal to `SA_LAB_KEY` is `lab`, and its `X-SA-Agent-Label` header is stored as the agent label. Anything else is `field`, with the MCP client name (if available) as the label. Compare keys with `hmac.compare_digest`. If headers cannot be read from tools in the installed FastMCP version, fall back to optional `begin_task` arguments and document it.
- **Per-call flow** for the five domain tools: validate session -> count the call against the budget -> run the world operation -> on a domain error call `render_error`; if it is styled and the session has no variant yet, call `ExperimentClient.style_for`, then `ledger.record_exposure` -> record the tool call in the ledger -> return the payload.
- **Budget.** `begin_task` and `finish_task` do not count. When the budget is exhausted the session is finalised with `budget_exhausted` and later calls return `BUDGET_EXHAUSTED`.
- **Finalisation** (one function, idempotent): run `check`, map to the outcome, `ledger.finish_session`, and if the session was exposed and in the experiment, `track("task_completed")` for `correct` or `track("task_wrong_booking")` for `incorrect`. `budget_exhausted` sessions are still scored on final state for the events, while keeping `budget_exhausted` as the ledger outcome plus a separate `final_state` value. Add that column to the ledger in this ticket.
- **Idle expiry.** A background task finalises sessions idle longer than `SA_SESSION_IDLE_SECONDS` as `abandoned` and frees their memory. Abandoned sessions send no events.
- **Never** include the variant key in any response.
- Entry points: `uv run python -m second_audience.booking.server --transport http --port 8000` and `--transport stdio`.
- `scripts/smoke.py`: an MCP client (official `mcp` Python SDK) with no model. For N sessions it begins a given scenario, makes the obvious failing call, then either applies a known correct solution or applies `corrected_call` when present, then finishes. Prints a table of outcomes by variant, read from the ledger.

**Checkpoint A procedure** (developer and agent together):

1. Create a temporary multivariate flag `error_style` in `development` with the two canonical values, and a throwaway experiment with metric event `task_completed`, using the SA-02 commands.
2. Run the server against `development`, then `scripts/smoke.py` for 40 sessions.
3. Refresh results. Confirm exposures and conversions per variant match the ledger.
4. Record the evidence in `docs/checkpoint-a.md`.

## 5. Dependencies

- Tickets: SA-03, SA-04, SA-05, SA-06
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- An MCP client can list the seven tools and complete a scenario end to end over HTTP.
- The same scenario run under each style returns error payloads that differ only by the added fields.
- A session that never triggers a styled error has `variant` null and sends no events.
- Budget exhaustion and idle expiry finalise sessions with the right outcomes.
- No response contains the variant key.
- Checkpoint A: Flagsmith exposures and `task_completed` counts match the ledger for the smoke run, with evidence in `docs/checkpoint-a.md`. If counts differ, the cause is explained.

## 7. Testing requirements

- Unit tests for `SessionManager` with `NullExperimentClient` and SQLite: budget, idempotent finalisation, idle expiry, lab versus field detection.
- In-process integration test driving the FastMCP app with an MCP client for one aligned and one trap scenario under each style.
- Manual: Checkpoint A.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-07: MCP server, session lifecycle and Checkpoint A`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-07-mcp-server`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-07: MCP server, session lifecycle and Checkpoint A` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-08. It can begin immediately once Checkpoint A passes. Do not start it if Checkpoint A fails.
