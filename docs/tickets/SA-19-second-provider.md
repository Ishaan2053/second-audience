# SA-19: Contingency: second free model provider

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P3 |
| Type | feature |
| Estimate | 2h |
| Depends on | SA-08. Start only on the trigger below. |
| Components | `lab/models.py`, `HYPOTHESIS.md` |
| Branch | `feat/SA-19-second-provider` |
| Can run in parallel with | Any |

## 1. Objective

Add one more free model provider behind `ModelClient` so the lab population can reach the planned sample when Gemini quotas cannot.

## 2. Context

The developer chose to run on Gemini's free tier only, and is open to another provider if needed. This ticket exists so that decision can be executed quickly. **Trigger:** after the SA-08 pilot or at any `sa status` check, the projected number of exposed sessions per arm by the hard end is below the stopping rule's minimum. Adding a model changes the population, so it must be recorded openly.

## 3. Scope

**In scope**

- One additional `ModelClient` implementation for a provider with a free tier.
- Configuration and pacing for it.
- An amendment section in `HYPOTHESIS.md`.

**Out of scope**

- Paid usage of any kind.
- More than one additional provider.
- Changes to the agent loop, system prompt or scenarios.

## 4. Implementation instructions

- Confirm the trigger with the developer and get their choice of provider. Check the provider's current free-tier limits and function-calling support with web search and Context7. Selection criteria: no payment card required; supports function calling with JSON Schema tools; daily quota meaningfully adds sessions.
- Implement the client to the existing `ModelClient` protocol only. The runner must need no changes beyond constructing the client from settings.
- Add settings for the provider's key and model IDs to `Settings` and `.env.example`.
- Run the same pilot as SA-08 (two sessions per scenario) in `development` before adding the model to the production plan.
- **Amend `HYPOTHESIS.md`** with a dated "Amendment" section: what changed, why, when, and that it was decided on sample-size grounds without reference to arm-level results. Commit this before the first production session from the new model.
- The report's per-model table (SA-12) already separates models; confirm the new model appears and add a sentence to the limitations noting the population changed mid-experiment.

## 5. Dependencies

- Tickets: SA-08. Start only on the trigger below.
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- The new model completes pilot sessions through the unchanged runner loop.
- The amendment is committed before any production session from the new model, verifiable from git and ledger timestamps.
- No paid resource was used.
- The report shows the new model as a separate row.

## 7. Testing requirements

- Unit tests for the new client's request and response mapping with the SDK mocked.
- `network`-marked test: one real session.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-19: Contingency: second free model provider`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P3` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-19-second-provider`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-19: Contingency: second free model provider` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

Return to the main sequence.
