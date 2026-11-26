# Risk Register

Impact and likelihood: H high, M medium, L low.

| # | Risk | Type | L | I | Mitigation | Fallback |
|---|---|---|---|---|---|---|
| R1 | Gemini free-tier quotas cap the sample below about 150 exposed sessions | External | M | H | Measure quotas in SA-02; start runs at Checkpoint B; spread across models and quota-days; pace requests | SA-19: add a second free provider behind `ModelClient` |
| R2 | Flagsmith results endpoint lacks win probability or interval fields | Integration | M | M | Inspect the JSON in SA-02 | Read those figures from the dashboard and record them with a screenshot; compute rates from the ledger |
| R3 | Events take a long time to reach results | Integration | M | M | Measure latency in SA-02; stop lab runs with a buffer before the report | Report from the ledger with Flagsmith screenshots taken at cut-off |
| R4 | Lazy exposure not supported cleanly by the SDK | Technical | M | L | Check with Context7 in SA-06 | Eager exposure at `begin_task` |
| R5 | No API to end an experiment or set the winning value | Integration | M | L | Investigate in SA-14 | CLI prints the exact dashboard steps and records the decision in the audit log |
| R6 | Free host sleeps, cold-starts slowly, or drops state | Deployment | H | M | Ledger in hosted Postgres; control-style fallback when Flagsmith is slow; keep-alive ping | Demo field mode locally through a tunnel-free recording; cite ledger rows |
| R7 | Public endpoint abused or flooded | Security | L | M | Rate limits, budgets, concurrency cap, no secrets on the host beyond SDK key and database URL | Disable field sessions with one setting; lab runs continue locally |
| R8 | Result is inconclusive | Product | M | M | Scenarios designed for a meaningful effect; trap versus aligned split gives a second finding | Report it as inconclusive with the interval and the sample needed; the criteria ask for explainable results, not a winner |
| R9 | Models differ in function-calling behaviour and break the loop | Technical | M | M | Pilot each model in SA-08 before the main run | Drop a misbehaving model and record why in `HYPOTHESIS.md` before the experiment starts |
| R10 | Scenarios turn out trivial (agents check availability first and never see an error) | Product | M | M | Pilot measures the exposure rate; briefs name a specific room and time | Adjust scenarios before the experiment starts, never after |
| R11 | Scope creep into dashboards or extra experiments | Scope | M | H | Non-goals in `CLAUDE.md`; tickets define out-of-scope | Apply the cut order |
| R12 | Estimates exceed the 20-28 hour budget | Time | H | H | Cut order fixed in advance; checkpoints | Ship MVP plus field endpoint; skip SA-13 to SA-15 |
| R13 | Changing anything mid-experiment invalidates the result | Product | M | H | Freeze scenarios, heuristic, system prompt and models at Checkpoint B; record the commit hash | If a change is unavoidable, end the experiment, document it, and start a new one |
| R14 | Secrets leak into the public repository | Security | L | H | `.env` ignored; `.env.example` only; secret scanning in CI | Rotate the key immediately |
| R15 | Demo recording hits a live failure | Demo | M | M | Replay kit uses stored transcripts; saved results JSON | Screen-record sections separately |
| R16 | Field sessions add noise to the experiment | Product | L | L | Sessions tagged; report shows results with and without field | Exclude field in the headline number and say so |
| R17 | Windows-specific breakage (paths, subprocess, event loop) | Technical | M | L | Use `pathlib`, argument lists; developer runs each ticket locally | Run the server in Docker |

## Scope-creep guard

Any work not named in a ticket requires a new ticket and an explicit decision about what
it displaces.
