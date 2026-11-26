# Checkpoint A: pipeline proven

**Result: passed on 2026-11-26.** For 40 scripted sessions, Flagsmith's exposures and
conversions per variant match the ledger exactly.

## Setup

All in the Flagsmith `Development` environment of project `second-audience`, created for this
run and deleted afterwards:

| Item | Value |
|---|---|
| Flag | `error_style` (feature 305450), multivariate; control value and variant `prescriptive` (option 37209) set to `ARM_FLAG_VALUES` |
| Metrics | `task_completed` (611, occurrence, up, expected `increase`); `task_wrong_booking` (612, occurrence, down, expected `not_increase`) |
| Experiment | 620 `checkpoint-a-smoke`, 100% rollout, 50/50 split, started 11:18:35Z |

The ticket asks only for `task_completed`; the guardrail metric was added so both event
counts could be reconciled.

Server and client:

- `uv run python -m second_audience.booking.server --transport http --port 8768`, with the
  Development server key, a temporary `SA_LAB_KEY`, and a dedicated local SQLite ledger (so
  scripted sessions stay out of the shared Postgres ledger).
- `uv run python scripts/smoke.py --url http://127.0.0.1:8768/mcp --sessions 40`, cycling
  through the twelve scenarios. Each session makes the first attempt (a styled error), then
  follows `corrected_call` when present and the known solution otherwise, then finishes.
  So control sessions should all end correct, and prescriptive sessions should end incorrect
  on trap scenarios.

## Ledger (smoke script output, 11:21:36Z to 11:21:53Z)

```text
connected to http://127.0.0.1:8768/mcp; tools: begin_task, book_room, cancel_booking, check_availability, finish_task, list_rooms, reschedule_booking

40 sessions in the ledger (0 unfinished)
sources: {'lab': 40}
outcomes: {'correct': 31, 'incorrect': 9}
```

| Variant | Sessions | Exposed in experiment | Correct | Incorrect | Expected `task_completed` | Expected `task_wrong_booking` |
|---|---|---|---|---|---|---|
| control | 22 | 22 | 22 | 0 | 22 | 0 |
| prescriptive | 18 | 18 | 9 | 9 | 9 | 9 |

## Flagsmith

Warehouse connection at 11:22:32Z:

```text
status=pending_connection total_events_received=80 last_event_received_at=2026-11-26T11:21:53Z
```

80 = 40 exposures + 31 `task_completed` + 9 `task_wrong_booking`.

`POST .../experiments/620/results/refresh/` returned `202 Accepted`; then
`GET .../experiments/620/results/` (as of 2026-11-26T11:22:34.902350Z):

```text
exposures  new_identities {'control': 22, 'prescriptive': 18}
611 task_completed      control n=22 sum=22.0   prescriptive n=18 sum=9.0   inference {'prescriptive': None}
612 task_wrong_booking  control n=22 sum=0.0    prescriptive n=18 sum=9.0   inference {'prescriptive': None}
```

## Reconciliation

| Variant | Exposures (Flagsmith / ledger) | `task_completed` (Flagsmith / ledger) | `task_wrong_booking` (Flagsmith / ledger) |
|---|---|---|---|
| control | 22 / 22 | 22 / 22 | 0 / 0 |
| prescriptive | 18 / 18 | 9 / 9 | 9 / 9 |

No differences. Inference is `null` because each arm has fewer than 50 identities (see
`docs/spike-findings.md`, question 5). Prescriptive's 9 wrong bookings are the trap scenarios,
where `corrected_call` violates the brief. That is the behaviour the experiment measures, here
produced by a script, not a model.

## Two failed attempts before this run

The first two 40-session runs ended with every session outside the experiment (variant
`None`, no exposures, no events). `FLAGSMITH_ENVIRONMENT_KEY` in the local `.env` is the
**Production** server key, so the flag was evaluated in Production, where no experiment ran.
The second attempt meant to override it, but a PowerShell 5.1 `ConvertFrom-Json` quirk left the
override empty, and pydantic-settings ignores empty variables, so it fell back to `.env`. The
passing run used a launcher that fetches the Development server key and aborts if it is
missing. Those runs (and a 13-session dry run) created about 93 throwaway identities in
Production, with no exposures and no events.
