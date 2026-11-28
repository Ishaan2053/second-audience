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
| SA-12 | Operator: status and report | done | #26 | #27 | Interim live report: inconclusive (23/25 of 75 per arm); reconciliation exact. Gemini prose number-checked |
| SA-13 | Operator: propose an experiment from failure logs | done | #28 | #29 | Live run proposed prescriptive + caution_note (not hard-coded); drafts not committed. Same-flag start needs a cleaned flag after SA-14 |
| SA-14 | Operator: ship the decision with approval | done (code) | #30 | #31 | API path (`complete/`, verified in SA-02). Records the decision before verifying; smoke only after a `completed` read-back, as lab traffic, excluded from analysis. Tested with fakes and a read-only live dry run (refused, 23/25 of 75); no Flagsmith write while 680 runs. Development rehearsal passed on 2026-11-28 (SA-14c, both rollouts); `v0.3.0` tag still to do |
| SA-14b | Ship hardening and review follow-ups before SA-17 | done | #36 | #37 | Guarded read-back (nested option ids), `ship_attempt` audit on a failed `complete/`, record-only re-run for a matching completed experiment, staleness warnings, lab-only smoke label, 6 s smoke wait; propose number check strips model labels and own names; demo fallback fixture in production shape. Tested with fakes only (680 live); Development rehearsal done in SA-14c |
| SA-14c | Record the Development ship rehearsal | done (docs) | #38 | #39 | Live AC passed on Development for `variant` (1634) and `control` (1637): ended, flag read back, smoke style matched, re-run refused. Feature-state option ids are bare ints. `is_final` needs a refresh after ship; deleting a flag deletes its experiments. `network` test still flaky (1 of 3 failed) |
| SA-15 | Demo kit: side-by-side transcript replay | done | #32 | #33 | Read-only replay and pair finder (DB-enforced read-only). Replays chosen: trap s05, aligned s08 (gemini-3.1-flash-lite); counts in `docs/demo-script.md` are a 2026-11-27 snapshot. Test clip recorded in SA-16b: readable at 100%, not on a phone (zoom in the edit) |
| SA-16 | README, results write-up and documentation | done | #34 | #35 | Interim numbers (Flagsmith as of 2026-11-26 18:02 UTC); final numbers, decision, video link and the final-results screenshot are `SA-17 PLACEHOLDER`s; the other Flagsmith screenshots added in SA-16b |
| SA-16b | Flagsmith screenshots and demo test clip | done | #40 | #41 | Five dashboard screenshots in `docs/img/` (flag, experiment, metrics, exposures, interim results), taken 2026-11-28; final results page stays in SA-17. Shot 2a test clip findings in `docs/demo-script.md` |
| SA-17 | Release hardening, final results and submission | pending | | | |
| SA-18 | Stretch: public middleware API and a second example server | done | #42 | #43 | `SecondAudience` in `core/middleware.py` (composes `ExperimentClient` + optional `Ledger`; styles stay `ErrorStyle`, events stay the three metrics). `examples/inventory_server/` on its own flag `SA_EXAMPLE_FLAG_NAME` (refused if equal to `SA_FLAG_NAME`); booking server untouched. Offline tests only; `network` tests not run while 680 is live. Merge restarts the Render service (src changed). Manual: run the `network` tests for the inventory example after experiment 680 ends; expect dropped in-flight field sessions on the redeploy |
| SA-19 | Contingency: second free model provider | pending | | | |
| SA-20 | Add-on: operational controls as Flagsmith flags | in review | #44 | #45 | `field_enabled` and `field_limits` created in Production and Development (defaults equal the settings, so no behaviour change on creation). `core/controls.py` caches 60 s, falls back per value to `Settings`; `/healthz` shows `controls` with sources. Toggle tested in Development only (FIELD_CLOSED after 49 s, lab unaffected; limits and malformed config applied without a restart). Merge redeploys Render (src changed) and drops in-flight field sessions. Manual: after the deploy, confirm `/healthz` shows `controls.source` = `flagsmith`; do not toggle `field_enabled` on Production while 680 runs unless needed |
| SA-22 | Add-on: Slack, GitHub and audit-log integrations | in review | #46 | #PR | `POST /hooks/flagsmith-audit` (`booking/audit_hook.py`): HMAC-SHA-256 `X-Flagsmith-Signature` under `SA_WEBHOOK_TOKEN`, 16 KiB cap, trimmed record (no author email) in `audit_log` as `flagsmith_audit`; route absent without the token. `sa report` ends with an "Independent audit trail" table. Free plan: webhook, Slack and GitHub endpoints answer 200, but `audit_log_visibility_days` is 0 and the audit log is a Scale-Up feature, so webhook delivery is unverified. Manual: set `SA_WEBHOOK_TOKEN` on Render and create the org audit-log webhook (`docs/deployment.md` section 11), toggle a Development flag and confirm a `flagsmith_audit` row (or record it as unavailable on free); Slack and GitHub OAuth, link `error_style` to #18, screenshots. Merge redeploys Render (src changed) and drops in-flight field sessions. Offline tests only; an accidental full `pytest` run also ran the `network` Flagsmith (temporary flag in Development, deleted) and Postgres (throwaway schema, dropped) tests, no Gemini calls |

## Checkpoints and releases

| Item | Status | Date | Evidence |
|---|---|---|---|
| Checkpoint A (pipeline proven) | passed | 2026-11-26 | `docs/checkpoint-a.md` |
| Checkpoint B (experiment live) | passed | 2026-11-26 | `v0.1.0` release notes |
| `v0.2.0` public endpoint | passed | 2026-11-26 | `v0.2.0` release notes, `docs/deployment.md` section 9 |
| `v0.3.0` operator complete | pending | | |
| `v1.0.0` submission | pending | | |
