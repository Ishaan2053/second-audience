# Implementation Phase Roadmap

Estimates are developer hours including supervising the coding agent and reviewing its output.
Total for P0 and P1 tickets: about 31 hours against a 20-28 hour budget, so the cut order
in the master plan is expected to be used. P0 alone is about 26 hours.

| Phase | Tickets | Objective | Outcome | Est. | Target (IST) |
|---|---|---|---|---|---|
| 0 Foundations | SA-01, SA-02 | Repo, CI, Flagsmith verified | Green CI; `docs/spike-findings.md` | 2.5h | Nov 26 |
| 1 Reference domain | SA-03, SA-04 | Booking logic, scenarios, error styles | Tested pure-Python core | 3.5h | Nov 26 |
| 2 Experiment plumbing | SA-05, SA-06, SA-07 | Ledger, Flagsmith client, MCP server | **Checkpoint A** | 5h | Nov 27 morning |
| 3 Go live | SA-08, SA-09 | Lab runner, operator start | **Checkpoint B**, `v0.1.0` | 5h | Nov 27 night |
| 4 Field | SA-10, SA-11 | Hardening and public deployment | Public URL, `v0.2.0` | 3.5h | Nov 28 midday |
| 5 Operator | SA-12, SA-13, SA-14 | Report, propose, ship | `v0.3.0` | 6h | Nov 28 night |
| 6 Submission | SA-15, SA-16, SA-17 | Demo kit, docs, release | `v1.0.0`, submitted | 5.5h | Nov 29, 6:00 PM |
| Stretch | SA-18 | Middleware API example | | 2h | only if ahead |
| Contingency | SA-19 | Second provider | | 2h | only if quotas force it |

## Checkpoints

**Checkpoint A (end of Phase 2): pipeline proven.**
A scripted client (no model) runs sessions against the local server in the `development`
Flagsmith environment. Exposures and events appear in Flagsmith and reconcile with the
ledger. Nothing in Phase 3 starts until this passes.

**Checkpoint B (end of Phase 3): experiment live.**
`HYPOTHESIS.md` is committed, the production experiment is running, and lab sessions are
flowing. From here the runner works in the background until Nov 29 around noon.

## Phase detail

### Phase 0: Foundations
- **Dependencies:** none. SA-02's account steps can be done by the developer while the agent does SA-01.
- **Acceptance:** CI green on `main`; a throwaway experiment in `development` returned results JSON.
- **Risks:** results endpoint lacks fields we need; event latency is long. Both are findings, not blockers.

### Phase 1: Reference domain
- **Dependencies:** SA-01.
- **Acceptance:** every scenario's obvious first attempt produces a styled error; trap scenarios' suggestions fail the checker; aligned ones pass.
- **Risks:** scenarios too easy or too hard. Tuned in the pilot (SA-09).

### Phase 2: Experiment plumbing
- **Dependencies:** Phase 1, SA-02.
- **Acceptance:** Checkpoint A.
- **Risks:** lazy exposure awkward in the SDK. Fallback: exposure at `begin_task`.

### Phase 3: Go live
- **Dependencies:** Phase 2.
- **Acceptance:** Checkpoint B.
- **Risks:** model quotas; function-calling quirks. Mitigations in the risk register.

### Phase 4: Field
- **Dependencies:** SA-07. Can overlap with Phase 5.
- **Acceptance:** an external MCP client completes a task against the public URL and the session appears in the ledger as `field`.
- **Risks:** free-host cold starts and limits.

### Phase 5: Operator
- **Dependencies:** SA-09 and some accumulated data.
- **Acceptance:** `sa report` produces the full report from live results; `sa propose` and `sa ship` work with approval.
- **Risks:** no documented API to end an experiment. Fallback: dashboard step guided by the CLI.

### Phase 6: Submission
- **Dependencies:** everything shipped so far.
- **Acceptance:** submission checklist complete.
- **Risks:** time. Recording starts no later than 2:00 PM IST on Nov 29.

## Parallel work

- SA-02 (developer) alongside SA-01 and SA-03 (agent).
- SA-05 and SA-06 are independent of each other.
- SA-10 and SA-11 are independent of SA-12 to SA-14.
- SA-13, SA-14 and SA-15 are independent of each other.

## Quota-days

Lab sample size is limited by daily model quotas, so sessions should start as early as
Checkpoint B allows. Confirm the quota reset time in SA-02 and schedule runs around it.
