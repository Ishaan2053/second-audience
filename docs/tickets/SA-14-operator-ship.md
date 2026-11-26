# SA-14: Operator: ship the decision with approval

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P1 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-12 |
| Components | `operator/ship.py`, `operator/cli.py`, `operator/flagsmith_admin.py` |
| Branch | `feat/SA-14-operator-ship` |
| Can run in parallel with | SA-13, SA-15 |

## 1. Objective

Implement `sa ship`: apply the pre-registered decision rule, show the human exactly what will change, and on typed confirmation end the experiment and roll out the winning value, or record a no-ship decision.

## 2. Context

This completes the hackathon's fourth step, "ship the winner", through the agent with a human approving. Whether Flagsmith exposes API calls to end an experiment and set the winning value is an open question from SA-02; the fallback keeps the human in control through the dashboard while the CLI still records the decision.

## 3. Scope

**In scope**

- `sa ship <slug> [--dry-run]`.
- Decision computed from the latest saved results and the spec's rule.
- Rollout through the API or CLI where supported, guided dashboard steps otherwise.
- Audit log entry and an appended "Decision" record in the report.
- Tag `v0.3.0`.

**Out of scope**

- Starting a follow-up experiment.
- Removing the flag or variant code.

## 4. Implementation instructions

- Refuse to run unless the stopping rule is met (same function as SA-12). No override flag.
- Decision: `ship_variant` if the primary metric's win probability for the variant meets the threshold and the guardrail metric is not credibly worse; `keep_control` if the control meets the threshold; otherwise `inconclusive` (which keeps control).
- Print: the rule, the numbers it was applied to, the decision, and the exact changes that will be made in Flagsmith.
- Require the user to type `ship <slug>` to proceed.
- **Investigate** (start from `docs/spike-findings.md` question 8, then the CLI's help output and Context7): the supported way to end or stop an experiment and to set the flag's default value to the winning variation. Decision criteria: use an API or CLI call only if it is documented or its effect was verified in `development`; never guess an endpoint against `production`.
- **If supported:** end the experiment, set the flag value, read both back and verify.
- **If not supported:** print numbered dashboard steps, wait for the user to confirm they are done, then read the flag and experiment state back through the API to verify.
- For `keep_control` and `inconclusive`: end the experiment the same way and leave the flag value unchanged.
- Verify the server now serves the expected style: run one smoke session and inspect the error payload.
- Write the `audit_log` row (action, decision, numbers, `approved_by`) and append a dated "Decision" section to `reports/<slug>/report.md`.
- Test the whole flow in `development` with a throwaway experiment before touching `production`.

## 5. Dependencies

- Tickets: SA-12
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `sa ship` refuses before the stopping rule is met.
- `--dry-run` prints the decision and planned changes and changes nothing.
- A wrong confirmation aborts with no changes.
- After a real run, the experiment is ended, the flag state matches the decision, and a smoke session confirms the served style.
- The audit log and the report record the decision and who approved it.
- The pull request states which path was used (API or guided dashboard) and why.

## 7. Testing requirements

- Unit tests for the decision function over fixture results: clear win, clear loss, inconclusive, guardrail violation.
- Unit tests for confirmation and dry run with the admin wrapper faked.
- Manual: full run in `development`. The `production` run happens in SA-17 at the cut-off.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-14: Operator: ship the decision with approval`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P1` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-14-operator-ship`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-14: Operator: ship the decision with approval` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-15, or SA-16 if short on time.
