# SA-03: Booking domain: world, scenarios and correctness checker

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 2h |
| Depends on | SA-01 |
| Components | `booking/world.py`, `booking/scenarios.py`, `booking/checker.py` |
| Branch | `feat/SA-03-booking-domain` |
| Can run in parallel with | SA-02 |

## 1. Objective

Implement the room-booking world as pure, fully tested Python: rooms, bookings, the twelve scenarios and the checker that decides whether a session's final state is correct.

## 2. Context

The reference server is the test subject for the experiment. Its success metric must be objective, so correctness is decided by a deterministic check on final state. Scenarios come in two kinds: aligned (the server's later suggestion fits the brief) and trap (it does not). This ticket has no MCP, Flagsmith or database code.

## 3. Scope

**In scope**

- Domain models: `Room`, `Booking`, `World`, with a fixed clock.
- World operations: list rooms, free slots, book, reschedule, cancel. Failures raise typed domain errors carrying structured details.
- Twelve scenarios (7 aligned, 5 trap) and a loader.
- `check(world, scenario) -> Outcome`.

**Out of scope**

- Error wording and suggestions (SA-04).
- Session budgets and lifecycle (SA-07).
- Anything asynchronous or networked.

## 4. Implementation instructions

- Fixed week: Monday 2027-03-15 to Friday 2027-03-19. Local times, 30-minute steps, `HH:MM` strings at the boundary and `datetime.time` inside.
- Six rooms `R1` to `R6` with varied capacity (2 to 16), features drawn from `projector`, `whiteboard`, `video`, and opening hours (most 08:00-20:00, one closing at 18:00).
- Bookings carry `booking_id`, `room_id`, `date`, `start`, `end`, `attendees`, `title`, `status` (`active` or `cancelled`) and `created_by` (`seed` or `agent`).
- Domain errors as exceptions with a `code` and a `details` dict: `RoomConflict` (conflicting interval), `CapacityExceeded` (capacity, requested), `OutsideOpeningHours` (opens, closes), `BookingNotFound`, `BookingCancelled`. Do not format user-facing text here.
- `Scenario` (frozen dataclass or pydantic model): `id`, `kind` (`aligned` or `trap`), `brief` (the text shown to the agent), `seed_bookings`, and `constraints`: `date`, `earliest_start`, `latest_end`, `duration_minutes`, `attendees`, `required_features`.
- Brief-writing rules, applied to every scenario:
  - Name a specific room and time for the first attempt, so the obvious call hits a styled error (conflict, capacity or opening hours).
  - State every constraint in plain words, including the ones a trap relies on (for example "it must finish by 12:00" or "we need a projector").
  - Use explicit ISO dates. No relative dates.
  - Each scenario must be solvable within the constraints.
- Spread the styled error types across scenarios: at least three conflict, two capacity and two opening-hours cases.
- `Outcome` enum: `correct`, `incorrect`, `no_booking` (plus `budget_exhausted` and `abandoned`, assigned by the session layer in SA-07).
- `check`: `correct` when exactly one active agent-made booking exists and it satisfies all constraints (date, window, duration, room capacity >= attendees, room has required features); `no_booking` when no active agent-made booking exists; otherwise `incorrect`.
- Keep scenario data in Python, not YAML: it is small and benefits from type checking.

## 5. Dependencies

- Tickets: SA-01
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- All twelve scenarios load, with exactly 7 `aligned` and 5 `trap`.
- For every scenario, a test performs the brief's obvious first attempt and gets the intended domain error.
- For every scenario, a test applies a known valid solution and `check` returns `correct`.
- `check` returns `incorrect` for: wrong window, missing feature, insufficient capacity, two active agent bookings.
- No imports from MCP, Flagsmith, SQLAlchemy or any network library in these modules.

## 7. Testing requirements

- Unit tests for every world operation including boundaries (back-to-back bookings do not conflict; end time equal to closing time is allowed).
- Parametrised tests over all scenarios for the three properties in the acceptance criteria.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-03: Booking domain: world, scenarios and correctness checker`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-03-booking-domain`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-03: Booking domain: world, scenarios and correctness checker` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-04. It can begin immediately.
