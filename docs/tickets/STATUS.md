# Ticket Status

Statuses: `pending`, `in progress`, `blocked`, `done`, `cut`. Update at the end of every ticket.

| ID | Title | Status | Issue | PR | Notes |
|---|---|---|---|---|---|
| SA-01 | Repository scaffold, tooling and CI | done | #1 | #2 | CI green; setup-uv pinned to an exact version (see spike-findings) |
| SA-02 | Flagsmith setup and verification spike | done | #3 | #4 | Gemini RPM/RPD not readable (login page); SA-08 must read them |
| SA-03 | Booking domain: world, scenarios and correctness checker | done | #5 | #6 | Scenarios carry first_attempt and solution; seed bookings can be cancelled by agents (decide before pilot) |
| SA-04 | Error styles and suggestion heuristic | done | #7 | #8 | No scenario data changed; reschedules get same-room suggestions only |
| SA-05 | Session ledger | done | #9 | #10 | Verified on SQLite and on Supabase Postgres (22 network tests passed) |
| SA-06 | Experiment client for Flagsmith | done | #12 | #13 | Lazy exposure via get_experiment_flag; 2 s timeout, one retry; network test 9/11 split on Development |
| SA-07 | MCP server, session lifecycle and Checkpoint A | done | #14 | #15 | Checkpoint A exact match (40 sessions); local `.env` holds the Production Flagsmith key |
| SA-08 | Lab runner on Gemini | done | #16 | #17 | Pilot 68 sessions: exposure 24% (below ~60%; SA-09 must revisit scenarios); models gemini-3.1/3.5-flash-lite (2.5-flash-lite 404) |
| SA-09 | Operator: spec, doctor, start, and going live (Checkpoint B) | done | #18 | #19, #20 | Experiment 680 live on Production; 20-session reconciliation exact. Primary metric is tool calls per session (pilot ceiling on completion) |
| SA-10 | Field hardening for the public endpoint | done | #21 | #22 | Limits in `booking/limits.py`; refusals never reach the ledger. Leftmost X-Forwarded-For is spoofable (SA-11 to verify on host) |
| SA-11 | Public deployment | done | #23 | #24, #25 | Render free, Oregon (Supabase is Tokyo; region fixed at creation). First field session verified with Claude Code. Two-network IP-hash check still open |
| SA-12 | Operator: status and report | pending | | | |
| SA-13 | Operator: propose an experiment from failure logs | pending | | | |
| SA-14 | Operator: ship the decision with approval | pending | | | |
| SA-15 | Demo kit: side-by-side transcript replay | pending | | | |
| SA-16 | README, results write-up and documentation | pending | | | |
| SA-17 | Release hardening, final results and submission | pending | | | |
| SA-18 | Stretch: public middleware API and a second example server | pending | | | |
| SA-19 | Contingency: second free model provider | pending | | | |

## Checkpoints and releases

| Item | Status | Date | Evidence |
|---|---|---|---|
| Checkpoint A (pipeline proven) | passed | 2026-11-26 | `docs/checkpoint-a.md` |
| Checkpoint B (experiment live) | passed | 2026-11-26 | `v0.1.0` release notes |
| `v0.2.0` public endpoint | passed | 2026-11-26 | `v0.2.0` release notes, `docs/deployment.md` section 9 |
| `v0.3.0` operator complete | pending | | |
| `v1.0.0` submission | pending | | |
