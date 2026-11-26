# SA-08: Lab runner on Gemini

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | feature |
| Estimate | 2.5h |
| Depends on | SA-07, SA-02 (model list and quotas) |
| Components | `lab/models.py`, `lab/plan.py`, `lab/runner.py` |
| Branch | `feat/SA-08-lab-runner` |
| Can run in parallel with | SA-09 can be developed alongside by keeping to separate files |

## 1. Objective

Drive real agent sessions against the server using Gemini free-tier models, with pacing, quota handling and resumability, so the experiment has a population of subjects.

## 2. Context

The subjects of the experiment are agents. The runner is deliberately simple and neutral: it gives a model the task brief and the server's tools, relays tool calls, and stops. It must not know the variant, must not help the model, and must behave identically for every session. Free-tier quotas are the main limit on sample size, so the runner has to make every request count and survive being stopped and restarted.

## 3. Scope

**In scope**

- `ModelClient` protocol and `GeminiModelClient`.
- Run plan: models x scenarios x replicates, stored in `lab_runs`.
- Agent loop over an MCP client connection.
- Pacing, retry with backoff, daily-quota parking, resume.
- Command `uv run python -m second_audience.lab.runner` with `plan`, `run` and `status` subcommands.

**Out of scope**

- A second provider (SA-19).
- Parallel sessions beyond a small configurable concurrency.
- Any analysis of results (SA-12).

## 4. Implementation instructions

- Use Context7 for the current Google Gen AI Python SDK (function calling, tool declarations, error types for rate limits) and the `mcp` client SDK (streamable HTTP client, custom headers).
- `ModelClient` protocol: `name: str` and `async def step(messages, tools) -> ModelTurn`, where `ModelTurn` holds text and a list of tool calls. Keep provider types out of the runner.
- Convert MCP tool schemas to the model's function declarations programmatically. If the SDK rejects a JSON Schema keyword, strip only that keyword and log it; record what was stripped in `docs/spike-findings.md`, since it weakens schema strictness for that model.
- **Fixed system prompt** (store as a constant; it is quoted in `HYPOTHESIS.md` in SA-09):
  "You are an assistant with access to room-booking tools. Complete the user's request using the tools. Follow the user's requirements exactly. When you have finished, call finish_task with a one-sentence summary."
- **Per run:** open an MCP connection to `SA_SERVER_URL` with headers `X-SA-Lab-Key` and `X-SA-Agent-Label: <model name>`; call `begin_task(scenario_id)` from the runner; send the model a user message containing the brief and the `session_id`; loop: model turn -> execute each tool call through MCP -> feed results back; stop when the model returns no tool call, calls `finish_task`, or reaches 15 model turns; if `finish_task` was not called, the runner calls it.
- The model must not be given `begin_task`. Remove it from the tool list passed to the model.
- Default temperature; do not tune it. Record the SDK and model versions in the run's log line.
- **Plan:** `plan --replicates N` inserts one row per (model, scenario, replicate) with status `planned`, shuffled with a recorded seed. Calling `plan` again adds further replicates without duplicating.
- **Pacing:** a per-model limiter set from the quotas in the spike findings, with headroom. On a per-minute rate-limit error, back off and retry the same turn. On a daily-quota error, mark that model parked until the next reset and continue with other models. On any other error, mark the run `failed` with the reason and move on; failed runs are not retried automatically.
- A run interrupted mid-session leaves an orphan session that the server's idle expiry finalises as `abandoned`. The run row returns to `planned`.
- `status` prints counts by model and status, and the estimated requests used today.
- Concurrency: default 1, configurable up to 3.
- **Pilot:** run two sessions per scenario per model against the `development` environment. Record in the pull request: completion rate, exposure rate (share of sessions that hit a styled error), average model calls per session, and any model that misbehaves.

## 5. Dependencies

- Tickets: SA-07, SA-02 (model list and quotas)
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `plan`, `run` and `status` work from PowerShell.
- A pilot of at least 24 sessions per model completes with ledger rows showing model, scenario, outcome and tool calls.
- Stopping the runner with Ctrl+C and starting it again continues without repeating completed runs.
- A simulated daily-quota error parks one model while others continue (unit test with a fake `ModelClient`).
- No log line, prompt or message sent to the model contains the variant key.
- The pull request states the measured exposure rate and average model calls per session, and an estimate of sessions per quota-day.

## 7. Testing requirements

- Unit tests with a scripted fake `ModelClient` and an in-process server: full loop, turn limit, runner-side `finish_task`, parking, resume.
- `network`-marked test: one real session per configured model.
- Manual: the pilot.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-08: Lab runner on Gemini`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-08-lab-runner`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-08: Lab runner on Gemini` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-09. It can begin immediately; if the pilot's exposure rate is below about 60%, raise it with the developer before SA-09 freezes the scenarios.
