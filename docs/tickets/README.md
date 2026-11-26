# Ticket Backlog and Execution Order

Nineteen tickets. Each file has the same ten sections and can be handed to the coding agent on its own.

## Epics

| Epic | Tickets | Phase |
|---|---|---|
| A Foundations | SA-01, SA-02 | 0 |
| B Reference server | SA-03, SA-04, SA-07 | 1-2 |
| C Experiment core | SA-05, SA-06 | 2 |
| D Lab population | SA-08, SA-19 | 3 |
| E Operator agent | SA-09, SA-12, SA-13, SA-14 | 3, 5 |
| F Field endpoint | SA-10, SA-11 | 4 |
| G Submission | SA-15, SA-16, SA-17 | 6 |
| H Stretch | SA-18 | - |

## Backlog

| ID | Title | Priority | Type | Est. | Depends on |
|---|---|---|---|---|---|
| [SA-01](SA-01-repo-scaffold-ci.md) | Repository scaffold, tooling and CI | P0 | infrastructure | 1h | None |
| [SA-02](SA-02-flagsmith-spike.md) | Flagsmith setup and verification spike | P0 | investigation | 1.5h | SA-01 (to commit findings) |
| [SA-03](SA-03-booking-domain.md) | Booking domain: world, scenarios and correctness checker | P0 | feature | 2h | SA-01 |
| [SA-04](SA-04-error-rendering.md) | Error styles and suggestion heuristic | P0 | feature | 1.5h | SA-03 |
| [SA-05](SA-05-session-ledger.md) | Session ledger | P0 | feature | 1.5h | SA-01 |
| [SA-06](SA-06-experiment-client.md) | Experiment client for Flagsmith | P0 | feature | 1.5h | SA-02, SA-04 |
| [SA-07](SA-07-mcp-server.md) | MCP server, session lifecycle and Checkpoint A | P0 | feature | 2h | SA-03, SA-04, SA-05, SA-06 |
| [SA-08](SA-08-lab-runner.md) | Lab runner on Gemini | P0 | feature | 2.5h | SA-07, SA-02 (model list and quotas) |
| [SA-09](SA-09-operator-start.md) | Operator: spec, doctor, start, and going live (Checkpoint B) | P0 | feature | 2.5h | SA-07, SA-08 |
| [SA-10](SA-10-field-hardening.md) | Field hardening for the public endpoint | P0 | feature | 1.5h | SA-07 |
| [SA-11](SA-11-public-deploy.md) | Public deployment | P0 | infrastructure | 2h | SA-10, SA-05 (hosted Postgres) |
| [SA-12](SA-12-operator-report.md) | Operator: status and report | P0 | feature | 2.5h | SA-09 and accumulated experiment data |
| [SA-13](SA-13-operator-propose.md) | Operator: propose an experiment from failure logs | P1 | feature | 2h | SA-09, SA-12 (shared ledger queries) |
| [SA-14](SA-14-operator-ship.md) | Operator: ship the decision with approval | P1 | feature | 1.5h | SA-12 |
| [SA-15](SA-15-demo-kit.md) | Demo kit: side-by-side transcript replay | P1 | feature | 1.5h | SA-08 (recorded sessions in the ledger) |
| [SA-16](SA-16-readme-results.md) | README, results write-up and documentation | P0 | documentation | 2h | SA-12 (a generated report); SA-11 for the public URL |
| [SA-17](SA-17-release-submission.md) | Release hardening, final results and submission | P0 | release | 2h | SA-16; all tickets intended for the submission |
| [SA-18](SA-18-middleware-api.md) | Stretch: public middleware API and a second example server | P2 | feature | 2h | SA-07 |
| [SA-19](SA-19-second-provider.md) | Contingency: second free model provider | P3 | feature | 2h | SA-08 |

## Master execution order

Feed the tickets in this order. Lines marked "parallel" can overlap with the line above.

| Step | Ticket | When (IST) | Notes |
|---|---|---|---|
| 1 | SA-01 | Nov 26 | Agent. Developer creates the Flagsmith account at the same time. |
| 2 | SA-02 | Nov 26 | Parallel with step 3. Needs the developer for account and login steps. |
| 3 | SA-03 | Nov 26 | |
| 4 | SA-04 | Nov 26 | |
| 5 | SA-05 | Nov 26-27 | Developer creates the free Postgres database. |
| 6 | SA-06 | Nov 27 | Requires SA-02 merged. |
| 7 | SA-07 | Nov 27 | **Checkpoint A.** Stop here if it fails. |
| 8 | SA-08 | Nov 27 | Includes the pilot. |
| 9 | SA-09 | Nov 27 | **Checkpoint B.** Experiment live, `v0.1.0`. Experiment files frozen from here. |
| 10 | SA-10 | Nov 28 | Lab runner keeps working in the background from now on. |
| 11 | SA-11 | Nov 28 | Public URL, `v0.2.0`. |
| 12 | SA-12 | Nov 28 | |
| 13 | SA-13 | Nov 28 | P1. Cut third. |
| 14 | SA-14 | Nov 28 | P1. Cut second. `v0.3.0`. |
| 15 | SA-15 | Nov 29 | P1. Cut first among P1. |
| 16 | SA-16 | Nov 29 | Start by 11:00 at the latest. |
| 17 | SA-17 | Nov 29 | Cut-off by 12:00, submit by 18:00. |
| any | SA-18 | only if ahead | Stretch. First thing to drop. |
| any | SA-19 | only on trigger | Contingency for quotas. |

## How to hand a ticket to the agent

Paste this, replacing the ID:

```
Execute ticket SA-XX. Read CLAUDE.md, then docs/tickets/SA-XX-*.md, then
docs/tickets/STATUS.md. Follow the execution protocol in CLAUDE.md and finish
with the handoff.
```

## Rules that apply across tickets

- From Checkpoint B, these are frozen: `world.py`, `scenarios.py`, `checker.py`, `errors.py`,
  `suggestions.py`, tool schemas and descriptions, the lab system prompt and the model list.
- If a ticket's instructions conflict with what the code or Flagsmith actually does, stop and
  report it; do not improvise around it.
- Estimates total about 31 hours for P0 and P1. The budget is 20-28 hours, so expect to use the cut order.
