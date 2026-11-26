# SA-21: Add-on: per-model rollout with a Flagsmith segment

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P2 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-14 (experiment ended), amendment A1 (identity traits) applied before Checkpoint B |
| Components | `operator/ship.py`, `operator/flagsmith_admin.py` |
| Branch | `feat/SA-21-segment-rollout` |
| Can run in parallel with | SA-15, SA-16 |

## 1. Objective

Let `sa ship` roll out a winning error style to only the agent models it helped, using a Flagsmith segment on the `agent_label` trait and a segment override on the flag.

## 2. Context

The report breaks results down by model. If the prescriptive style helps some models and harms others, shipping it to everyone is the wrong decision. Flagsmith segments target identities by trait, and segment overrides give a segment its own flag value. Because sessions already carry an `agent_label` trait (amendment A1), the server needs no code change: it resolves the flag per identity and Flagsmith returns the right value. This turns the per-model finding into an action, which is a strong demo moment. It only makes sense once the experiment has ended, because the flag is locked while an experiment runs.

## 3. Scope

**In scope**

- `sa ship <slug> --per-model` proposing a per-model plan from the report's breakdown.
- Segment creation and flag segment override through the API, after typed confirmation.
- Verification with one smoke session per affected model label.

**Out of scope**

- Automatic per-model decisions without approval.
- Statistical claims beyond what the report states: per-model results are exploratory and the output must say so.

## 4. Implementation instructions

- **Investigate first** in `development` (Context7, CLI help, Flagsmith docs): how to create a segment with a rule on a trait, how to add a segment override for a multivariate flag's value, whether this is available on the free plan, and whether identity traits must be sent on the same call that resolves the flag. If any part is unavailable, stop and report; do not attempt it on `production`.
- Per-model rule (shown to the user, applied only on confirmation): include a model in the rollout segment when its exposed sample per arm is at least 20, its correct rate is higher under the variant, and its wrong-booking rate is not higher. Everything else keeps the control value. Print the table and the rule, and label it "exploratory, based on small per-model samples".
- Confirmation text: `ship <slug> per-model`.
- Create segment `sa-<slug>-rollout` with a rule matching the chosen `agent_label` values, then override `error_style` for that segment with the variant's value. Read both back to verify.
- Run one smoke session with a lab key and each affected label, plus one unaffected label, and check the served error style.
- Audit log row with the model list and numbers. Append to the report's Decision section.

## 5. Dependencies

- Tickets: SA-14 (experiment ended), amendment A1 (identity traits) applied before Checkpoint B
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `--per-model --dry-run` prints the table, the rule and the planned segment, and changes nothing.
- After confirmation, a session labelled with an included model receives the variant style and one labelled with an excluded model receives control, with no server change or restart.
- The output and the report mark the per-model decision as exploratory.
- The pull request states what was verified about plan availability.

## 7. Testing requirements

- Unit tests for the per-model rule over fixture breakdowns.
- Unit tests for confirmation and dry run with the admin wrapper faked.
- Manual: full run in `development`.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-21: Add-on: per-model rollout with a Flagsmith segment`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P2` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-21-segment-rollout`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-21: Add-on: per-model rollout with a Flagsmith segment` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-16.
