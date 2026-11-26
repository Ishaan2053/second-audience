# Hackathon Constraint Matrix

Source: the Toggle Wars event page, schedule page and the organisers' walkthrough post,
read on Nov 22-25, 2026, plus points the developer confirmed with the organisers.

## Facts

| Item | Value |
|---|---|
| Event | Toggle Wars: The Flagsmith Experimentation Hackathon (WeMakeDevs x Flagsmith) |
| Window | Nov 26, 8:00 AM IST to Nov 29, 8:00 PM IST, 2026 |
| Format | Online, free, solo or team |
| Submission | Repository, README, demo video, chosen track(s) |
| Prizes | Track 01 $2,000; Track 02 $1,000; Track 03 PS5 + GTA 6. One winner per track. |
| Judging | Use of Flagsmith; Impact; Creativity; Demo video. No published weights. |
| AI coding tools | Allowed; the entrant must understand and explain the build. |
| Confirmed by developer | One project may be submitted and may enter more than one track. |
| Confirmed by developer | Work must be built after the hackathon starts. |
| Confirmed by developer | Experimentation is available on Flagsmith's free plan. |
| Confirmed by developer | Real users are not weighted significantly above simulated traffic. |

## Requirements by class

| Requirement | Class | How we comply | Ticket |
|---|---|---|---|
| Flagsmith is a core part of the project | Mandatory | Flag, SDK events, results engine, CLI and API all in the main path | SA-06, SA-09 |
| Built within the window | Mandatory | First commit on or after Nov 26; history is public | SA-01 |
| Repo, README, demo video | Mandatory | Public repo, README mapped to criteria, video | SA-16, SA-17 |
| Track 01: clear hypothesis | Mandatory | `HYPOTHESIS.md` committed before the experiment starts | SA-09 |
| Track 01: versions behind a flag | Mandatory | `error_style` multivariate flag | SA-04, SA-09 |
| Track 01: metric from real events | Mandatory | `task_finished` (tool calls, primary), `task_completed` and `task_wrong_booking` sent by the server from scored sessions | SA-06, SA-07, SA-09 |
| Track 01: explainable results | Mandatory | `sa report` and `docs/RESULTS.md` | SA-12, SA-16 |
| Track 02: CLI, API or MCP use | Mandatory | `flagsmith` CLI and Admin API | SA-09 |
| Track 02: human approves changes | Mandatory | Typed confirmation on start and ship; audit log | SA-09, SA-14 |
| Track 02: clear output, real time saved | Mandatory | Plan preview, status, Markdown report | SA-09, SA-12 |
| Honest labelling of traffic | Mandatory (our standard) | Lab and field tagged and reported separately | SA-07, SA-12 |
| Trap scenarios and correctness metric | Competitive advantage | Makes the result non-obvious | SA-03 |
| Per-model breakdown | Competitive advantage | Ledger analysis in the report | SA-12 |
| Public endpoint anyone can connect to | Competitive advantage | Free-host deployment | SA-10, SA-11 |
| Operator drafts the spec from failure logs | Competitive advantage | `sa propose` | SA-13 |
| Rollout through the agent | Competitive advantage | `sa ship` | SA-14 |
| Reusable middleware API | Optional | `second_audience.core` public API and example | SA-18 |
| Second model provider | Contingency | Only if quotas cap the sample | SA-19 |

## Open items to confirm

| Item | Where to check | Owner |
|---|---|---|
| Video length or format limits | Submission form | Developer |
| How to select two tracks on the form | Submission form or Discord | Developer |
| Whether the public endpoint must stay up through judging | Discord | Developer |

## Self-imposed constraints

- Zero spend: Gemini free tier, free hosting, Flagsmith free plan.
- Nothing related to Konnect.
- Windows PowerShell must be able to run every documented command.
