# Master Engineering Plan

## 1. Project overview

**Final definition.** Second Audience is an experimentation layer for agent-facing
interfaces. It wraps an MCP server, assigns each agent session a variant from a Flagsmith
multivariate flag, records whether the session completed its task correctly, and lets an
operator agent run the experiment lifecycle with human approval.

**Problem.** A product now has two audiences. The interface humans use is A/B tested with
mature tooling. The interface agents use (tool descriptions, responses, error messages) is
written by taste. Server owners cannot tell whether a wording choice helps agents, misleads
them, or costs tokens for nothing, and the answer can differ by model and change when models
are updated.

**Solution.** Treat agents as experiment subjects. Because an MCP server's users are
exclusively agents, agent sessions are its real traffic.

**Core value proposition.** After a server has done everything code can do (strict schema,
correct validation), design choices remain in how it talks to agents. Second Audience
measures those choices instead of guessing.

**Target users.** Developers who own MCP servers or agent-facing APIs.
Primary use case: decide between two reasonable designs for a tool's responses using
measured task success.

**Key differentiators.**

- Subjects are agents, so lab traffic is legitimate and field traffic is possible from any MCP client.
- The metric is correct task completion checked against server state, not an LLM judge.
- Trap scenarios make the outcome non-obvious: a helpful suggestion can produce a wrong booking.
- The operator agent keeps a human in the approval path at start and at rollout.

**Track alignment.**

| Track | How the project satisfies it |
|---|---|
| 01 Test Something Real | One experiment with a pre-registered hypothesis, both versions behind a multivariate flag, a metric from real session events, and an explained result. |
| 02 AI Agents and Feature Flags | The `sa` operator agent uses the Flagsmith CLI and Admin API, saves setup and analysis time, requires human approval, and produces a readable report. |

**Judging alignment.**

| Criterion | Evidence we will show |
|---|---|
| Use of Flagsmith | Flag holds the variants; SDK records exposures and events; Bayesian engine decides; flag ships the winner; CLI and API drive it all. |
| Impact | Reusable middleware API, a public endpoint anyone can try, a concrete finding about error design. |
| Creativity | A/B testing applied to a place it is not used: agent-facing interfaces. |
| Demo video | Problem, side-by-side agent behaviour, operator flow, Flagsmith results, connect-your-own-agent. |

**Final deliverable.** A public GitHub repository at release `v1.0.0`, a README, a demo
video, a running public MCP endpoint, a completed Flagsmith experiment, and a results report.

## 2. Constraints and priorities

See `03-constraint-matrix.md` for the full matrix. Summary:

- **Deadline:** Nov 29, 2026, 8:00 PM IST. Internal deadline: 6:00 PM IST.
- **Resources:** one developer, 20-28 hours, local Claude Code as the implementing agent.
- **Money:** zero. Free-tier AI (Gemini), free hosting, Flagsmith Cloud free plan.
- **Mandatory:** Flagsmith as a core part; repo, README and demo video; built during the hackathon window.

## 3. Scope

**MVP (a complete, valid submission).**
Reference server with the two error styles; experiment client with exposure and events;
ledger; lab runner; `sa doctor` and `sa start`; a running experiment with lab sessions;
`sa report`; README; video.

**Core features (needed to demonstrate the value).**
Trap scenarios and correctness checking; per-model and per-scenario-kind breakdown; public
field endpoint; approval gates; pre-registered `HYPOTHESIS.md`.

**Secondary features.**
`sa propose` (failure-log analysis to draft a spec); `sa ship` (rollout with approval);
demo replay kit.

**Stretch goals.**
Public middleware API with a second example server (SA-18).

**Contingency only.**
A second free model provider (SA-19), built only if Gemini quotas cap the sample.

**Non-goals.** Listed in `CLAUDE.md`.

**Feature dependencies.**
Domain -> errors -> server; ledger and experiment client -> server; server -> runner;
runner and operator start -> live experiment; live experiment -> report, propose, ship;
server -> field hardening -> deployment.

**Cut order if time runs short:** SA-18, SA-15, SA-14, SA-13. Never cut SA-12, SA-16, SA-17.

## 4. Technical architecture

See `02-architecture.md`.

## 5. Implementation phases

See `04-roadmap.md`.

## 6. Risk management

See `05-risk-register.md`.

## 7. Demonstration and submission

**Demo flow (target three to four minutes; confirm any length limit on the submission form).**
The shots, commands, narration and fallbacks are in `docs/demo-script.md`.

1. **Problem (20s).** "You A/B test the interface humans use. The one agents use is never measured."
2. **Side by side (40s).** Two recorded agent sessions on the same trap scenario: one arm
   gets the informative error, the other the prescriptive one. Show what each agent does next.
3. **Operator (45s).** `sa propose` drafts the spec from pilot failure logs; `sa start`
   shows the plan, asks for approval, and creates the flag, metric and experiment.
4. **Results (45s).** Flagsmith's results page, then `sa report`: overall result, aligned
   versus trap scenarios, per-model table.
5. **Field (20s).** Connect an MCP client to the public URL and run a task.
6. **Ship (15s).** `sa ship` with approval, or the decision not to ship and why.
7. **Close (10s).** What a server owner does with this.

**Must work reliably in the demo:** the replay kit (recorded transcripts, no live model
call), `sa report` against stored results, the public endpoint's health page.

**Fallbacks.**

- Model quota exhausted during recording: use the replay kit.
- Public host asleep or down: show the local server, state the URL, show ledger rows from field sessions.
- Flagsmith results slow to refresh: use the last saved results JSON in `reports/`.

**Environment requirements.** Listed in `02-architecture.md` (configuration) and the README.

**Documentation set.** README, `docs/02-architecture.md`, `HYPOTHESIS.md`,
`docs/RESULTS.md`, environment reference, known limitations.

**Final checks.** See `07-submission-checklist.md`.
