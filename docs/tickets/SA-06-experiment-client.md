# SA-06: Experiment client for Flagsmith

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-02, SA-04 |
| Components | `core/experiment.py` |
| Branch | `feat/SA-06-experiment-client` |
| Can run in parallel with | SA-05 |

## 1. Objective

Wrap the Flagsmith Python SDK behind a small interface that assigns an error style per session, records the exposure at the right moment, sends conversion events, and degrades safely when Flagsmith is unreachable.

## 2. Context

This is the reusable heart of the middleware. Exposure is recorded lazily, the first time a session needs a styled error, because sessions that never see one are unaffected by the variant. `docs/spike-findings.md` (SA-02) states the exact SDK methods and whether lazy exposure is practical; follow its "Decisions for later tickets" section.

## 3. Scope

**In scope**

- `ExperimentClient` protocol, `FlagsmithExperimentClient`, `NullExperimentClient`.
- Lazy exposure with per-session caching.
- Events `task_completed` and `task_wrong_booking`.
- Fallback to the control style on any Flagsmith failure.
- A `network`-marked integration test against the `development` environment.

**Out of scope**

- Creating flags or experiments (SA-09).
- Ledger writes (the session layer does those in SA-07).

## 4. Implementation instructions

- Contract:
  ```python
  class ExperimentClient(Protocol):
      def style_for(self, session_id: str) -> tuple[ErrorStyle, str | None]: ...
      def track(self, session_id: str, event: str) -> None: ...
      def flush(self) -> None: ...
  ```
- `style_for` resolves flag `SA_FLAG_NAME` for identity `session_id` using the SDK call that also records the exposure, parses the value with `ErrorStyle.from_flag_value`, and returns `(style, variant_key)`. Cache per `session_id` so repeated calls neither re-evaluate nor re-expose. Bound the cache (for example an LRU of a few thousand entries).
- On any exception or timeout, return `(control_style, None)`. `None` tells the caller the session is not in the experiment. Log once per session.
- `track` sends the named event for the same identity. It must not raise. Only the two known event names are accepted; anything else raises `ValueError` at call time (a programming error, not a runtime failure).
- Initialise the SDK with events enabled and a default flag handler that returns the control value. Use the key type and options recorded in the spike findings. Use Context7 to confirm signatures for the installed version.
- Set a short network timeout so a slow Flagsmith never stalls a tool call for long.
- `flush` forwards to the SDK's flush or close. The server calls it on shutdown and the lab runner calls it at the end of a batch.
- `NullExperimentClient(style, variant)` returns a fixed style and records calls in memory for tests.
- If the spike found lazy exposure impractical, implement eager exposure behind the same interface and document the deviation in `docs/02-architecture.md`.

## 5. Dependencies

- Tickets: SA-02, SA-04
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- Two calls to `style_for` with the same `session_id` produce one SDK evaluation.
- With the SDK patched to raise, `style_for` returns the control style and `None`, and `track` does not raise.
- `track` with an unknown event name raises `ValueError`.
- Integration test: with a temporary multivariate flag in `development`, 20 identities receive a mix of both variants, and the same identity always receives the same variant.

## 7. Testing requirements

- Unit tests with the SDK mocked: caching, fallback, event validation, flush.
- `network`-marked integration test described above; the developer runs it and reports the result.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-06: Experiment client for Flagsmith`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-06-experiment-client`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-06: Experiment client for Flagsmith` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-07. It needs SA-03, SA-04, SA-05 and SA-06 merged.
