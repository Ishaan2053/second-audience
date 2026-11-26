# Report: error-style-v1

Decision: **inconclusive**. Results refreshed for this report.

Prose in sections 1 and 9: drafted by gemini-3.5-flash-lite from the computed numbers; every number checked. Every other line, and every number, is computed from the saved Flagsmith results, `manual.yaml` and the ledger.

## 1. Summary

This experiment tested if a prescriptive error style lowers the number of tool calls an AI agent needs to finish a task compared with an informative error, without lowering correct bookings or raising wrong ones. We measured tool calls per session, exposed sessions per arm, task completion correctly, and wrong booking. The decision is inconclusive because the stopping rule was not met and the test has not reached the hard end.

## 2. Setup

- Question: When a booking tool call fails on the current state of the world, an error that adds a suggested alternative and a corrected call (prescriptive) lowers the number of tool calls an agent needs to finish the task, compared with an error that states the fact (informative), without lowering correct bookings or raising wrong ones.
- Pre-registration: [HYPOTHESIS.md](../../HYPOTHESIS.md), frozen at commit `3679478b0c24014f42981cac37d94084e6edf3d2`
- Flagsmith: environment `production`, flag `error_style`, experiment 680
- Started: 2026-11-26 15:29:45 UTC; hard end: 2026-11-29 06:30:00 UTC
- Stopping rule: at least 75 exposed sessions per arm, or the hard end; ship only if the win probability on the primary metric is at least 0.95 and no guardrail is credibly worse
- Results: Flagsmith as of 2026-11-26 18:02:13 UTC, saved in `reports/error-style-v1/results-20261126T180211Z.json`; report generated 2026-11-26 18:02:19 UTC

| Arm | Variant key | Split | Flag value |
|---|---|---|---|
| Informative | `control` | 50% | `{"suggest_alternative": false, "corrected_call": false, "caution_note": false}` |
| Prescriptive | `prescriptive` | 50% | `{"suggest_alternative": true, "corrected_call": true, "caution_note": false}` |

| Role | Metric | Event | Aggregation | Expected direction |
|---|---|---|---|---|
| primary | Tool calls per session | `task_finished` | mean | decrease |
| guardrail | Task completed correctly | `task_completed` | occurrence | not_decrease |
| guardrail | Wrong booking | `task_wrong_booking` | occurrence | not_increase |

## 3. Headline result (from Flagsmith)

**Primary: Tool calls per session** (`task_finished`)

| Arm | Exposed (n) | Sum | Mean (sum / n) |
|---|---|---|---|
| control | 23 | 102 | 4.435 |
| prescriptive | 25 | 97 | 3.880 |

Lift, credible interval and win probability are withheld until the stopping rule is met.

**Guardrail: Task completed correctly** (`task_completed`)

| Arm | Exposed (n) | Sum | Rate (sum / n) |
|---|---|---|---|
| control | 23 | 23 | 1 |
| prescriptive | 25 | 23 | 0.920 |

Lift, credible interval and win probability are withheld until the stopping rule is met.

**Guardrail: Wrong booking** (`task_wrong_booking`)

| Arm | Exposed (n) | Sum | Rate (sum / n) |
|---|---|---|---|
| control | 23 | 0 | 0 |
| prescriptive | 25 | 0 | 0 |

Lift, credible interval and win probability are withheld until the stopping rule is met.


## 4. Reconciliation

Ledger counts against Flagsmith's, per arm. "Ledger at as_of" counts only what the ledger had recorded by Flagsmith's as_of time, which separates event latency from lost events.

| Measure | Arm | Ledger | Ledger at as_of | Flagsmith | Difference | Likely cause |
|---|---|---|---|---|---|---|
| Exposed sessions | control | 23 | 23 | 23 | 0 | match |
| task_finished (sum) | control | 102 | 102 | 102 | 0 | match |
| task_completed (sum) | control | 23 | 23 | 23 | 0 | match |
| task_wrong_booking (sum) | control | 0 | 0 | 0 | 0 | match |
| Exposed sessions | prescriptive | 25 | 25 | 25 | 0 | match |
| task_finished (sum) | prescriptive | 97 | 97 | 97 | 0 | match |
| task_completed (sum) | prescriptive | 23 | 23 | 23 | 0 | match |
| task_wrong_booking (sum) | prescriptive | 0 | 0 | 0 | 0 | match |

Sessions exposed while Flagsmith was unreachable (fallback, excluded from both sides by design): 0. Enrolled sessions still open (no events sent yet): 0.

## 5. Split health

| Arm | Configured | Expected | Observed (Flagsmith) |
|---|---|---|---|
| control | 50% | 24 | 23 |
| prescriptive | 50% | 24 | 25 |

Chi-square (1 degree of freedom) = 0.083, p = 0.7728: no sample-ratio mismatch at p < 0.01. Flagsmith's own SRM p-value: not reported.

## 6. Breakdowns (ledger)

**Exploratory: not pre-registered as decision criteria; small samples.**

Scored sessions only: exposed, in the experiment, and finished (not abandoned). Rates use the final booking state; intervals are 95% Wilson intervals.

### All exposed sessions

| Group | Arm | n | Correct (95% CI) | Wrong booking (95% CI) | Mean tool calls |
|---|---|---|---|---|---|
| all | control | 23 | 100.0% (85.7% to 100.0%) | 0.0% (0.0% to 14.3%) | 4.43 |
| all | prescriptive | 25 | 92.0% (75.0% to 97.8%) | 0.0% (0.0% to 13.3%) | 3.88 |

### By scenario kind

| Group | Arm | n | Correct (95% CI) | Wrong booking (95% CI) | Mean tool calls |
|---|---|---|---|---|---|
| aligned | control | 14 | 100.0% (78.5% to 100.0%) | 0.0% (0.0% to 21.5%) | 4.29 |
| aligned | prescriptive | 12 | 83.3% (55.2% to 95.3%) | 0.0% (0.0% to 24.2%) | 3.25 |
| trap | control | 9 | 100.0% (70.1% to 100.0%) | 0.0% (0.0% to 29.9%) | 4.67 |
| trap | prescriptive | 13 | 100.0% (77.2% to 100.0%) | 0.0% (0.0% to 22.8%) | 4.46 |

### By model

| Group | Arm | n | Correct (95% CI) | Wrong booking (95% CI) | Mean tool calls |
|---|---|---|---|---|---|
| claude-code | control | 0 | n/a | n/a | n/a |
| claude-code | prescriptive | 1 | 100.0% (20.7% to 100.0%) | 0.0% (0.0% to 79.3%) | 3 |
| gemini-3.1-flash-lite | control | 5 | 100.0% (56.6% to 100.0%) | 0.0% (0.0% to 43.4%) | 4.40 |
| gemini-3.1-flash-lite | prescriptive | 8 | 100.0% (67.6% to 100.0%) | 0.0% (0.0% to 32.4%) | 3.75 |
| gemini-3.5-flash-lite | control | 18 | 100.0% (82.4% to 100.0%) | 0.0% (0.0% to 17.6%) | 4.44 |
| gemini-3.5-flash-lite | prescriptive | 16 | 87.5% (64.0% to 96.5%) | 0.0% (0.0% to 19.4%) | 4 |

### By source

| Group | Arm | n | Correct (95% CI) | Wrong booking (95% CI) | Mean tool calls |
|---|---|---|---|---|---|
| field | control | 0 | n/a | n/a | n/a |
| field | prescriptive | 1 | 100.0% (20.7% to 100.0%) | 0.0% (0.0% to 79.3%) | 3 |
| lab | control | 23 | 100.0% (85.7% to 100.0%) | 0.0% (0.0% to 14.3%) | 4.43 |
| lab | prescriptive | 24 | 91.7% (74.2% to 97.7%) | 0.0% (0.0% to 13.8%) | 3.92 |

## 7. Traffic

All sessions started since 2026-11-26 15:29:45 UTC.

| Source | Model | Sessions | Exposed | Share exposed | In experiment | Excluded (Flagsmith unreachable) |
|---|---|---|---|---|---|---|
| field | claude-code | 1 | 1 | 100.0% | 1 | 0 |
| lab | gemini-3.1-flash-lite | 41 | 13 | 31.7% | 13 | 0 |
| lab | gemini-3.5-flash-lite | 49 | 34 | 69.4% | 34 | 0 |

| Source | Model | correct | incorrect | no_booking | budget_exhausted | abandoned | open |
|---|---|---|---|---|---|---|---|
| field | claude-code | 1 | 0 | 0 | 0 | 0 | 0 |
| lab | gemini-3.1-flash-lite | 39 | 1 | 0 | 0 | 1 | 0 |
| lab | gemini-3.5-flash-lite | 45 | 0 | 2 | 0 | 2 | 0 |

## 8. Limitations

- Lab traffic comes from two small Gemini Flash-Lite models on free quota; results may not carry over to larger models or other providers.
- Twelve synthetic scenarios in one fictional office; the trap share (5 of 12) is a design choice, not an estimate of how often real suggestions are wrong.
- Flagsmith's results include lab and field sessions together (its results cannot be split by trait); the breakdowns by source come from the ledger.
- The primary metric counts tool calls, not outcomes; the two guardrails watch outcomes.
- Breakdowns are exploratory and were not used for the decision.
- Breakdown cells with fewer than 20 scored sessions: by scenario kind: aligned / control (n = 14); by scenario kind: aligned / prescriptive (n = 12); by scenario kind: trap / control (n = 9); by scenario kind: trap / prescriptive (n = 13); by model: claude-code / control (n = 0); by model: claude-code / prescriptive (n = 1); by model: gemini-3.1-flash-lite / control (n = 5); by model: gemini-3.1-flash-lite / prescriptive (n = 8); by model: gemini-3.5-flash-lite / control (n = 18); by model: gemini-3.5-flash-lite / prescriptive (n = 16); by source: field / control (n = 0); by source: field / prescriptive (n = 1).
- Flagsmith returned no inference for: Tool calls per session, Task completed correctly, Wrong booking.
- Field sessions included in Flagsmith's counts: 1.
- The stopping rule is not met: this is an interim report.

## 9. Decision

**Decision: inconclusive.**

The stopping rule was not met because exposed sessions per arm were control 23 and prescriptive 25, which are below the minimum required number 75, and the hard end 2026-11-29 has not passed. Therefore, no winner is named before then and the result remains inconclusive.

Rule applied (pre-registered in HYPOTHESIS.md section 10):

- Stopping rule not met: exposed sessions per arm are control 23, prescriptive 25, below 75, and the hard end 2026-11-29T12:00:00+05:30 has not passed. No winner is named before then.
