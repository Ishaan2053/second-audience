# SA-12: Operator: status and report

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 2.5h |
| Depends on | SA-09 and accumulated experiment data |
| Components | `operator/report.py`, `operator/cli.py`, `reports/` |
| Branch | `feat/SA-12-operator-report` |
| Can run in parallel with | SA-10, SA-11, SA-13 |

## 1. Objective

Implement `sa status` and `sa report`: fetch Flagsmith results, apply the pre-registered stopping rule, combine with ledger breakdowns, and produce a Markdown report a person can read and trust.

## 2. Context

Track 01 is judged on explainable results, and Track 02 on clear output. This command produces both. It must be honest: it does not name a winner before the stopping rule is met, it labels lab and field traffic, it marks breakdowns as exploratory, and it reconciles its two data sources. The model writes prose only; every number is computed in code.

## 3. Scope

**In scope**

- `sa status <slug>` and `sa report <slug>`.
- Saved raw results under `reports/<slug>/results-<timestamp>.json`.
- Report at `reports/<slug>/report.md`.
- Ledger queries for breakdowns.

**Out of scope**

- Charts or HTML output.
- Statistics beyond what is listed (no custom Bayesian model; Flagsmith's engine is the source of truth for the headline).
- Changing the experiment (SA-14).

## 4. Implementation instructions

- **Results parsing.** Map the results JSON into a typed `ExperimentResults` model using the field names recorded in `docs/spike-findings.md`. If a headline field (win probability, interval) is absent from the API, accept it through a small `reports/<slug>/manual.yaml` the developer fills from the dashboard, and mark those values "from dashboard" in the report.
- **`sa status`** prints: experiment state; exposed sessions per arm against `min_exposed_per_arm`; time to `hard_end`; ledger totals by source. If the stopping rule is not met it says so and shows no win probability and no leader. `--peek` overrides this, prints a clear warning, and writes an `audit_log` row.
- **Stopping rule met** when both arms reach the minimum exposure, or `hard_end` has passed.
- **`sa report`** refreshes results (respecting the 5-minute refresh limit; reuse the latest saved file when refused), saves the raw JSON, and writes these sections:
  1. Summary: the question, the decision per the pre-registered rule, in three or four sentences.
  2. Setup: flag, arms, metrics, dates, commit hash of the freeze, link to `HYPOTHESIS.md`.
  3. Headline result (from Flagsmith): per arm exposures, conversions, rate; lift; credible interval; win probability; the same for the guardrail metric.
  4. Reconciliation: ledger counts versus Flagsmith counts per arm, with the difference and likely cause if any (event latency, fallback sessions).
  5. Split health: observed versus configured split with a chi-square test for sample-ratio mismatch (implement directly; one degree of freedom).
  6. Breakdowns from the ledger, each a table per arm with n, correct rate and wrong-booking rate: by scenario kind (aligned, trap); by model; by source (lab, field); mean tool calls per session. Head the section "Exploratory: not pre-registered as decision criteria; small samples."
  7. Traffic: sessions by source and model; share exposed; outcomes including `abandoned` and `budget_exhausted`; sessions excluded because Flagsmith was unreachable.
  8. Limitations: fixed text plus computed caveats (for example any cell with n under 20).
  9. Decision: ship, do not ship, or inconclusive, with the rule applied.
- Rates in breakdowns use exposed, in-experiment sessions only. Show a simple interval for each rate (Wilson) so small cells are not over-read.
- **Narrative.** Sections 1 and 9 are drafted by Gemini from a JSON object of the computed numbers, with an instruction to use only those numbers. After generation, extract every number from the prose and verify each appears in the input; on any mismatch, or with no model key, fall back to a deterministic template. `--no-llm` forces the template.
- The report states which sections were written by a model.

## 5. Dependencies

- Tickets: SA-09 and accumulated experiment data
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `sa status` before the stopping rule is met shows progress and no winner.
- `sa report` produces all nine sections from live data and saves the raw results JSON.
- Every number in the report is traceable to the saved results JSON, the manual file, or a ledger query.
- The narrative check rejects a draft containing a number not in its input (unit test) and the template is used.
- `sa report --no-llm` works with no model key.
- The reconciliation table is present and any difference is explained.

## 7. Testing requirements

- Unit tests with fixture results JSON and a seeded SQLite ledger: stopping rule, breakdown maths, Wilson interval, chi-square, narrative number check, template fallback.
- Manual: run against the live experiment and read the report critically.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-12: Operator: status and report`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-12-operator-report`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-12: Operator: status and report` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-13, SA-14 and SA-15 are independent. Recommended: SA-13, then SA-14, then SA-15. If under time pressure, skip to SA-16.
