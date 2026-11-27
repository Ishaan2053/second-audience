# Results: error-style-v1

> **Interim results.** The experiment is still running and its stopping rule is not met.
> Everything here is an interim read, written for review before the cut-off. Items marked
> `SA-17 PLACEHOLDER` are filled in from the final Flagsmith results and the final
> `sa report` at the cut-off (ticket SA-17).

**Data cut-off (interim):** Flagsmith results as of **2026-11-26 18:02:13 UTC**, saved in
[`reports/error-style-v1/results-20261126T180211Z.json`](../reports/error-style-v1/results-20261126T180211Z.json);
report generated 2026-11-26 18:02:19 UTC. A read-only `sa status` at 2026-11-27 12:29 UTC
showed the live ledger with the same exposed counts (control 23, prescriptive 25), so no
sessions arrived between the two.

**Final data cut-off:** [SA-17 PLACEHOLDER: Flagsmith `as_of` of the final results and the
saved results file name]

Every number below is copied from the generated report,
[`reports/error-style-v1/report.md`](../reports/error-style-v1/report.md), which computes
them from the saved Flagsmith results and the ledger. Traffic is labelled by source
throughout: **lab** sessions are driven by the lab runner on Gemini models; **field**
sessions come from anyone's agent on the public endpoint.

## 1. What was tested

Pre-registration: [`HYPOTHESIS.md`](../HYPOTHESIS.md), frozen at commit
`3679478b0c24014f42981cac37d94084e6edf3d2`, before the start. Spec:
[`experiments/error-style-v1.yaml`](../experiments/error-style-v1.yaml).

- **Question.** When a booking tool call fails on the current state of the world, does an
  error that adds a suggested alternative and a corrected call (**prescriptive**) lower the
  number of tool calls an agent needs, compared with an error that states the fact
  (**informative**, control), without lowering correct bookings or raising wrong ones?
- **Flagsmith:** project `second-audience`, environment `production`, flag `error_style`,
  experiment 680. Started 2026-11-26 15:29:45 UTC; hard end 2026-11-29 06:30:00 UTC.
- **Stopping rule:** at least 75 exposed sessions per arm, or the hard end. Ship
  `prescriptive` only if its win probability on the primary metric is at least 0.95 and
  neither guardrail is credibly worse.

| Arm | Variant key | Split | Flag value |
|---|---|---|---|
| Informative | `control` | 50% | `{"suggest_alternative": false, "corrected_call": false, "caution_note": false}` |
| Prescriptive | `prescriptive` | 50% | `{"suggest_alternative": true, "corrected_call": true, "caution_note": false}` |

| Role | Metric | Event | Aggregation | Expected direction |
|---|---|---|---|---|
| Primary | Tool calls per session | `task_finished` | mean | decrease |
| Guardrail | Task completed correctly | `task_completed` | occurrence | not decrease |
| Guardrail | Wrong booking | `task_wrong_booking` | occurrence | not increase |

A session is exposed at its first styled error. Twelve scenarios: seven **aligned** (the
suggestion satisfies the brief) and five **traps** (the suggestion breaks the brief, yet a
correct booking exists).

## 2. Headline (from Flagsmith, interim)

Flagsmith's results combine lab and field sessions (they cannot be split by trait): 47 lab
and 1 field exposed session.

| Metric | Arm | Exposed (n) | Sum | Mean or rate |
|---|---|---|---|---|
| Tool calls per session | control | 23 | 102 | 4.435 |
| Tool calls per session | prescriptive | 25 | 97 | 3.880 |
| Task completed correctly | control | 23 | 23 | 1 |
| Task completed correctly | prescriptive | 25 | 23 | 0.920 |
| Wrong booking | control | 23 | 0 | 0 |
| Wrong booking | prescriptive | 25 | 0 | 0 |

Lift, credible interval and win probability are withheld until the stopping rule is met.
At this point Flagsmith returned no inference for any of the three metrics (it needs at least
50 identities per arm).

**Final headline:** [SA-17 PLACEHOLDER: final per-arm n, sums and means, and Flagsmith's
lift, 95% credible interval and win probability for each metric]

## 3. Decision

**Interim decision: inconclusive.** Exposed sessions per arm are control 23 and prescriptive
25, below the pre-registered 75, and the hard end has not passed, so no winner is named
(`HYPOTHESIS.md` section 10).

**Final decision:** [SA-17 PLACEHOLDER: decision from `sa ship` (`ship_variant`,
`keep_control` or `inconclusive`), the rule steps it applied, and the verified Flagsmith
state]

## 4. Does the ledger agree with Flagsmith?

The server writes every session to its own ledger. `sa report` compares, per arm, the
ledger's exposures and event sums with Flagsmith's. In the interim report every row matched
exactly:

| Measure | control (ledger / Flagsmith) | prescriptive (ledger / Flagsmith) |
|---|---|---|
| Exposed sessions | 23 / 23 | 25 / 25 |
| `task_finished` (sum) | 102 / 102 | 97 / 97 |
| `task_completed` (sum) | 23 / 23 | 23 / 23 |
| `task_wrong_booking` (sum) | 0 / 0 | 0 / 0 |

Sessions excluded because Flagsmith was unreachable: 0. Enrolled sessions still open: 0.

**Split health.** Configured 50/50; observed control 23, prescriptive 25 (expected 24 each).
Chi-square = 0.083, p = 0.7728: no sample-ratio mismatch at p < 0.01.

## 5. Lab and field, separately (ledger)

Scored sessions only: exposed, in the experiment, finished (not abandoned). Intervals are 95%
Wilson intervals.

| Source | Arm | n | Correct (95% CI) | Wrong booking (95% CI) | Mean tool calls |
|---|---|---|---|---|---|
| lab | control | 23 | 100.0% (85.7% to 100.0%) | 0.0% (0.0% to 14.3%) | 4.43 |
| lab | prescriptive | 24 | 91.7% (74.2% to 97.7%) | 0.0% (0.0% to 13.8%) | 3.92 |
| field | control | 0 | n/a | n/a | n/a |
| field | prescriptive | 1 | 100.0% (20.7% to 100.0%) | 0.0% (0.0% to 79.3%) | 3 |

The one field session was Claude Code (client name `claude-code`) on scenario `s08`, which
booked correctly in 3 calls after the prescriptive suggestion.

## 6. Exploratory breakdowns (ledger)

**Not pre-registered as decision criteria; small samples.** All exposed sessions (lab and
field together, as in the report).

By scenario kind:

| Kind | Arm | n | Correct (95% CI) | Wrong booking (95% CI) | Mean tool calls |
|---|---|---|---|---|---|
| aligned | control | 14 | 100.0% (78.5% to 100.0%) | 0.0% (0.0% to 21.5%) | 4.29 |
| aligned | prescriptive | 12 | 83.3% (55.2% to 95.3%) | 0.0% (0.0% to 24.2%) | 3.25 |
| trap | control | 9 | 100.0% (70.1% to 100.0%) | 0.0% (0.0% to 29.9%) | 4.67 |
| trap | prescriptive | 13 | 100.0% (77.2% to 100.0%) | 0.0% (0.0% to 22.8%) | 4.46 |

By model:

| Model (source) | Arm | n | Correct (95% CI) | Mean tool calls |
|---|---|---|---|---|
| gemini-3.1-flash-lite (lab) | control | 5 | 100.0% (56.6% to 100.0%) | 4.40 |
| gemini-3.1-flash-lite (lab) | prescriptive | 8 | 100.0% (67.6% to 100.0%) | 3.75 |
| gemini-3.5-flash-lite (lab) | control | 18 | 100.0% (82.4% to 100.0%) | 4.44 |
| gemini-3.5-flash-lite (lab) | prescriptive | 16 | 87.5% (64.0% to 96.5%) | 4 |
| claude-code (field) | prescriptive | 1 | 100.0% (20.7% to 100.0%) | 3 |

What these interim numbers show, and what they do not:

- **Traps did not catch the agents.** No session in either arm made a wrong booking, on traps
  or elsewhere. Every trap session ended correct in both arms. In the prescriptive arm, agents
  sent the offered corrected call unchanged as their next call in 0 of 13 trap sessions and 3
  of 12 aligned sessions (from `sa propose`'s evidence pack, 2026-11-26).
- **The two failures are both prescriptive, on aligned scenarios.** Both ended with no
  booking (`s08` and `s10`, gemini-3.5-flash-lite, 1 tool call each), which is why the
  prescriptive completion rate is 23 of 25.
- **Fewer calls in the prescriptive arm so far** (3.880 against 4.435 per session), mostly
  on aligned scenarios (3.25 against 4.29). With 23 and 25 sessions and no inference from
  Flagsmith yet, this is not evidence of an effect.

## 7. Traffic

All sessions started since 2026-11-26 15:29:45 UTC.

| Source | Model | Sessions | Exposed | Share exposed | correct | incorrect | no_booking | abandoned |
|---|---|---|---|---|---|---|---|---|
| field | claude-code | 1 | 1 | 100.0% | 1 | 0 | 0 | 0 |
| lab | gemini-3.1-flash-lite | 41 | 13 | 31.7% | 39 | 1 | 0 | 1 |
| lab | gemini-3.5-flash-lite | 49 | 34 | 69.4% | 45 | 0 | 2 | 2 |

Sessions that never hit a styled error are not exposed and are not part of the comparison
(the one `incorrect` lab session above was not exposed).

## 8. Limitations

- Lab traffic comes from two small Gemini Flash-Lite models on free quota; results may not
  carry over to larger models or other providers.
- Twelve synthetic scenarios in one fictional office; the trap share (5 of 12) is a design
  choice, not an estimate of how often real suggestions are wrong.
- Flagsmith's results include lab and field sessions together; the split by source comes from
  the ledger.
- The primary metric counts tool calls, not outcomes; the two guardrails watch outcomes.
- Breakdowns are exploratory and were not used for the decision; every cell by scenario kind
  and by model holds fewer than 20 sessions.
- Each session is its own Flagsmith identity, so one agent can appear in both arms.
- The stopping rule is not met: this is an interim read.

## Flagsmith screenshots

Taken by the developer from the Flagsmith dashboard (project `second-audience`, environment
`production`) and stored in `docs/img/`. Each will be embedded here at SA-17:

| File | What it shows | Referenced from |
|---|---|---|
| `docs/img/flagsmith-flag.png` | The `error_style` multivariate flag: control value and the `prescriptive` variant value | here; README "Use of Flagsmith" |
| `docs/img/flagsmith-experiment-setup.png` | Experiment `error-style-v1` (680): arms, 50/50 split, the three metrics with roles and directions | here; README "Use of Flagsmith" |
| `docs/img/flagsmith-exposures.png` | The experiment's exposures panel: exposed identities per arm | here; README "Use of Flagsmith" |
| `docs/img/flagsmith-results.png` | The results page at the cut-off: per-metric lift, credible intervals and win probability | here; README "Use of Flagsmith" and "What we tested" |

[SA-17 PLACEHOLDER: embed the four screenshots above]
