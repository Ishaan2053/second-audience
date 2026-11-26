# SA-02: Flagsmith setup and verification spike

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | investigation |
| Estimate | 1.5h |
| Depends on | SA-01 (to commit findings). Account steps need the developer. |
| Components | Flagsmith project, `docs/spike-findings.md` |
| Branch | `chore/SA-02-flagsmith-spike` |
| Can run in parallel with | SA-01, SA-03, SA-04 |

## 1. Objective

Stand up the Flagsmith project and prove, with a throwaway experiment, exactly how exposures, events and results behave, so later tickets are written against verified facts.

## 2. Context

Several details are unverified and affect the design: the Python SDK's experiment methods, which key type remote evaluation needs, how fast events reach results, what the results endpoint returns, whether results can be segmented by trait, and current Gemini free-tier quotas. Experimentation is in beta, so documentation may lag behaviour.

## 3. Scope

**In scope**

- Developer: create the Flagsmith Cloud organisation and project `second-audience` with environments `development` and `production`; enable the managed warehouse on both; install the `flagsmith` CLI v2+ and run `flagsmith login`.
- A throwaway script `scripts/spike_flagsmith.py` that sends exposures and events for about 40 synthetic identities to `development`.
- A throwaway experiment created and started through the CLI and Admin API.
- `docs/spike-findings.md` answering every question below.

**Out of scope**

- The real flag, metrics or experiment.
- Any production-environment traffic.
- Reusable client code (that is SA-06).

## 4. Implementation instructions

Use Context7 for the Flagsmith Python SDK and CLI before writing the script. Follow the flow in Flagsmith's "Set up with a coding agent" documentation:

- `flagsmith feature create <flag> --value control --enabled`
- `flagsmith feature variant add <flag> --value <value> --key <key> --weight 0`
- `POST api/v1/environments/<env>/experiment-metrics/` with `name`, `aggregation: "occurrence"`, `direction: "up"`, `definition: {version: 1, event}`
- `POST api/v1/environments/<env>/experiments/` with `feature`, `name`, `hypothesis`, `metrics`, `experiment_rollout`
- `POST .../experiments/<id>/start/`, then `POST .../results/refresh/` (limited to once per 5 minutes) and `GET .../results/`
- Check `GET api/v1/environments/<env>/warehouse-connections/` first.

Questions `docs/spike-findings.md` must answer, each with evidence (command and trimmed output):

1. Python SDK: package version installed; exact names and signatures for resolving an experiment flag with exposure, for resolving without exposure plus a separate exposure call, and for tracking an event; the option that enables events; how to flush.
2. Which environment key type the server should use for remote evaluation.
3. Whether a JSON string works as a multivariate flag value and any length limit seen.
4. Event-to-results latency (time from first event to non-empty results).
5. The full shape of the results JSON: which fields give exposures per variation, conversions, lift, credible interval and win probability.
6. Whether an experiment accepts more than one metric, and how a guardrail metric appears.
7. Whether results can be filtered or segmented by an identity trait.
8. Whether there is an API call to stop or end an experiment and to set the winning value (needed in SA-14).
9. Free-plan limits that matter: requests per month, identities, environments.
10. Gemini free tier: for each candidate model, requests per minute, requests per day, and the quota reset time in IST. Take these from the AI Studio rate-limit page. Recommend the two or three models to use.

Clean up: stop and delete the throwaway experiment and flag. Never print or commit the admin credential.

## 5. Dependencies

- Tickets: SA-01 (to commit findings). Account steps need the developer.
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- `docs/spike-findings.md` answers all ten questions with evidence, or marks a question "could not determine" with what was tried.
- The throwaway experiment showed non-zero exposures and conversions in results.
- A "Decisions for later tickets" section states: lazy or eager exposure for SA-06, the model list for SA-08, and the SA-14 approach.
- Throwaway flag and experiment removed.

## 7. Testing requirements

- Manual verification only. Paste the commands run and their real output into the findings file.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-02: Flagsmith setup and verification spike`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `chore/SA-02-flagsmith-spike`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-02: Flagsmith setup and verification spike` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-03 if not yet done; otherwise SA-05 or SA-06. SA-06 must not start before this ticket is merged.
