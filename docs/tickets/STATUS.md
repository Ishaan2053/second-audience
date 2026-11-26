# Ticket Status

Statuses: `pending`, `in progress`, `blocked`, `done`, `cut`. Update at the end of every ticket.

| ID | Title | Status | Issue | PR | Notes |
|---|---|---|---|---|---|
| SA-01 | Repository scaffold, tooling and CI | done | #1 | #2 | CI green; setup-uv pinned to an exact version (see spike-findings) |
| SA-02 | Flagsmith setup and verification spike | done | #3 | #4 | Gemini RPM/RPD not readable (login page); SA-08 must read them |
| SA-03 | Booking domain: world, scenarios and correctness checker | done | #5 | #6 | Scenarios carry first_attempt and solution; seed bookings can be cancelled by agents (decide before pilot) |
| SA-04 | Error styles and suggestion heuristic | done | #7 | #8 | No scenario data changed; reschedules get same-room suggestions only |
| SA-05 | Session ledger | pending | | | |
| SA-06 | Experiment client for Flagsmith | pending | | | |
| SA-07 | MCP server, session lifecycle and Checkpoint A | pending | | | |
| SA-08 | Lab runner on Gemini | pending | | | |
| SA-09 | Operator: spec, doctor, start, and going live (Checkpoint B) | pending | | | |
| SA-10 | Field hardening for the public endpoint | pending | | | |
| SA-11 | Public deployment | pending | | | |
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
| Checkpoint A (pipeline proven) | pending | | `docs/checkpoint-a.md` |
| Checkpoint B (experiment live) | pending | | `v0.1.0` release notes |
| `v0.2.0` public endpoint | pending | | |
| `v0.3.0` operator complete | pending | | |
| `v1.0.0` submission | pending | | |
