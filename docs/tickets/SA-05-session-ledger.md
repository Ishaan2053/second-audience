# SA-05: Session ledger

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-01 |
| Components | `core/ledger.py` |
| Branch | `feat/SA-05-session-ledger` |
| Can run in parallel with | SA-04, SA-06 |

## 1. Objective

Provide a durable record of every session, tool call, lab run and approval, working on SQLite for tests and Postgres for the real run.

## 2. Context

Flagsmith holds the experiment's exposures and conversions. The ledger holds what Flagsmith does not: which model and scenario each session used, the transcript of tool calls, and an audit trail of operator actions. Reports, the per-model breakdown, `sa propose` and reconciliation all read from it. The public host has an ephemeral disk, so the real run uses a free hosted Postgres database that the developer creates.

## 3. Scope

**In scope**

- Tables `sessions`, `tool_calls`, `lab_runs`, `audit_log` as in `docs/02-architecture.md` section 3.6.
- `Ledger` class with the methods listed below.
- Schema creation on start-up (`create_all`); no migration tool.
- Developer step: create a free hosted Postgres database and put its URL in `.env` as `SA_DATABASE_URL`.

**Out of scope**

- Alembic migrations.
- Any ORM models or relationships.
- Report queries (added in SA-12 as needed).

## 4. Implementation instructions

- SQLAlchemy 2.x Core with `MetaData` and `Table` definitions. Add the Postgres driver recommended by current SQLAlchemy documentation (check Context7). JSON columns use SQLAlchemy's generic `JSON` type so both databases work.
- `Ledger(engine)` methods (synchronous is acceptable; keep calls short):
  - `start_session(session_id, scenario_id, scenario_kind, source, agent_label, client_ip_hash)`
  - `record_exposure(session_id, variant, in_experiment)`
  - `record_tool_call(session_id, seq, tool, arguments, result, error_code, styled)`
  - `finish_session(session_id, outcome, tool_call_count)`
  - `plan_runs(rows)`, `claim_next_run(model)`, `complete_run(run_id, session_id)`, `fail_run(run_id, reason)`, `run_counts()`
  - `audit(action, experiment_slug, detail, approved_by)`
  - `failing_sessions(limit)` returning sessions with outcome not `correct`, with their tool calls
- Timestamps in UTC. `finish_session` is idempotent: a second call does not change the stored outcome.
- Truncate stored `result` payloads above a sensible size (for example 8 KB) and mark them truncated.
- Never store raw IP addresses. The caller passes a salted hash.
- Provide `make_engine(settings)` with `pool_pre_ping=True` for Postgres.
- If the developer has not created the Postgres database yet, complete the ticket on SQLite and note the pending step in the handoff.

## 5. Dependencies

- Tickets: SA-01
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- All methods work against an in-memory SQLite database in tests.
- The same test suite passes against Postgres when `SA_DATABASE_URL` points to one (run locally, marked `network`).
- `finish_session` called twice keeps the first outcome.
- `claim_next_run` never returns the same run twice.
- No raw IP address can be written through the public API of `Ledger`.

## 7. Testing requirements

- Unit tests on SQLite for every method.
- One `network`-marked test module that runs the same assertions on Postgres.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-05: Session ledger`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-05-session-ledger`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-05: Session ledger` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-06 if not done, otherwise SA-07.
