# SA-04: Error styles and suggestion heuristic

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-03 |
| Components | `core/styles.py`, `booking/errors.py`, `booking/suggestions.py` |
| Branch | `feat/SA-04-error-rendering` |
| Can run in parallel with | SA-02, SA-05 |

## 1. Objective

Turn domain errors into the two response designs under test, driven entirely by a style configuration, and implement the fixed heuristic that generates suggestions.

## 2. Context

This is the experiment's treatment. The control arm states the fact; the prescriptive arm adds a suggested alternative and a corrected call. The style is configuration parsed from the flag value, so a new variant can be rolled out without a redeploy. The heuristic deliberately knows nothing about the user's constraints, which is what makes trap scenarios meaningful.

## 3. Scope

**In scope**

- `ErrorStyle` model and `ErrorStyle.from_flag_value(str | None)`.
- `render_error(error, world, style, attempted_call) -> dict`.
- `suggest(error, world, attempted_call) -> Suggestion | None`.
- Tests proving trap scenarios trap and aligned scenarios align.

**Out of scope**

- Reading the flag (SA-06).
- MCP tool wiring (SA-07).

## 4. Implementation instructions

- `ErrorStyle` fields, all booleans defaulting to false: `suggest_alternative`, `corrected_call`, `caution_note`. `from_flag_value` parses JSON; on `None`, empty or invalid input it returns the all-false control style and logs a warning. Unknown keys are ignored. `corrected_call` without `suggest_alternative` is treated as false.
- Styled codes: `ROOM_CONFLICT`, `CAPACITY_EXCEEDED`, `OUTSIDE_OPENING_HOURS`. Other errors render identically regardless of style.
- Base payload (both arms): `{"error": {"code": ..., "message": ...}}`. Messages state the specific fact, for example which interval is taken, the room's capacity, or its opening hours. Write them once and reuse them in both arms, so the only difference between arms is the added fields.
- With `suggest_alternative`: add `"suggestion": {"room_id", "date", "start", "end", "reason"}`.
- With `corrected_call`: add `"corrected_call": {"tool": <same tool as attempted>, "arguments": {...}}` built from the attempted call with the suggestion applied.
- With `caution_note`: add `"note"` telling the agent to check the suggestion against the user's requirements. Implement and test it; experiment 1 does not use it.
- Heuristic, exactly as specified in `docs/02-architecture.md` section 3.3. It must be deterministic. If no alternative exists, return `None` and omit the suggestion fields.
- `render_error` returns a tuple or small result object that also reports whether the error was styled, so the session layer can record exposure only for styled errors.
- Add a module-level constant with the canonical flag values for both arms, used later by the operator and by tests:
  - control: `{"suggest_alternative": false, "corrected_call": false, "caution_note": false}`
  - prescriptive: `{"suggest_alternative": true, "corrected_call": true, "caution_note": false}`

## 5. Dependencies

- Tickets: SA-03
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- For the same error, the prescriptive payload equals the informative payload plus `suggestion` and `corrected_call`; `error.code` and `error.message` are byte-identical.
- For each of the 5 trap scenarios, applying the `corrected_call` from the first styled error produces a state the checker scores `incorrect`.
- For each of the 7 aligned scenarios, applying it produces `correct`.
- Invalid or missing flag values yield the control style without raising.
- The heuristic returns the same suggestion on repeated calls with the same world.

## 7. Testing requirements

- Unit tests for parsing, each styled code in each style, the no-alternative case and tie-breaking.
- Parametrised scenario tests for the trap and aligned properties. If a scenario fails its property, fix the scenario data in this ticket and say so in the pull request.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-04: Error styles and suggestion heuristic`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-04-error-rendering`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-04: Error styles and suggestion heuristic` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-05 and SA-06 (independent of each other). SA-06 requires SA-02 to be merged.
