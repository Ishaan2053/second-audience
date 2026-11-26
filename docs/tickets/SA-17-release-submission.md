# SA-17: Release hardening, final results and submission

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | release |
| Estimate | 2h |
| Depends on | SA-16; all tickets intended for the submission |
| Components | Whole repository, release, submission |
| Branch | `chore/SA-17-release-submission` |
| Can run in parallel with | None |

## 1. Objective

Close the experiment, finalise the numbers, run the mandatory review and readiness checks, cut `v1.0.0`, and complete the submission before 6:00 PM IST on Nov 29.

## 2. Context

This ticket is the only place `/code-review` and `/ship` are mandatory under the lean workflow. It also performs the experiment cut-off, which is a one-way step. Work through it in order and do not skip the verification steps. The hard deadline is 8:00 PM IST; the internal deadline leaves two hours of buffer.

## 3. Scope

**In scope**

- Experiment cut-off and final report.
- `/code-review` over the codebase and `/ship` readiness check.
- Fixes for critical and high findings.
- Final numbers and screenshots in the README and `docs/RESULTS.md`.
- `v1.0.0` tag and release; submission form.

**Out of scope**

- New features.
- Refactors not required by a review finding.
- Any change to frozen experiment files.

## 4. Implementation instructions

Suggested timeline for Nov 29 (IST): cut-off by 12:00, review and fixes by 14:00, video recorded by 16:00, submitted by 18:00.

1. **Cut-off.** Stop the lab runner. Wait for the event latency measured in SA-02, then `sa report <slug>`. Check reconciliation. The developer takes the Flagsmith screenshots listed in SA-16.
2. **Decision.** Run `sa ship <slug>` on `production` (or, if SA-14 was cut, end the experiment in the dashboard and record the decision manually in the report and audit log). Re-run `sa report` so the decision section is included.
3. **Final numbers.** Replace every placeholder in `README.md` and `docs/RESULTS.md`. Search the repository for the placeholder marker to confirm none remain.
4. **`/code-review`.** Run it across the codebase. Record each finding with severity in the pull request. Fix critical and high findings. List the rest under "Known limitations" in the README if they are user-visible. Do not touch frozen experiment files; if a finding is in one, document it instead.
5. **Checks.** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`, the `network` tests locally, `docker build`. Record actual results.
6. **`/ship`.** Run it and record what it reported: completeness, build and test status, deployment readiness, configuration. Resolve blockers.
7. **Secrets.** Run the secret scan over full history. Confirm `.env` was never committed.
8. **Deployment.** Confirm the public endpoint runs the release commit (`/healthz`) and that an external client completes a task.
9. **Release.** Merge, tag `v1.0.0`, publish a GitHub release with notes: what it is, the experiment result in two sentences, links to the README sections and the video.
10. **Video.** The developer records using `docs/demo-script.md` and uploads it. Add the link to the README before tagging if possible. If the video is finished after the tag, commit the link to `main` and mention it in the release notes.
11. **Submit.** Complete `docs/07-submission-checklist.md` item by item. Select Track 01 and Track 02. Save the confirmation.

## 5. Dependencies

- Tickets: SA-16; all tickets intended for the submission
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- The experiment is ended, the final report is committed, and the decision is recorded.
- No placeholders remain in the README or results write-up.
- `/code-review` findings are listed with their resolution; no critical or high finding is open.
- `/ship` was run and its actual output is summarised in the pull request.
- CI is green on the release commit; `v1.0.0` tag and GitHub release exist.
- The public endpoint serves the release commit.
- Every box in `docs/07-submission-checklist.md` is ticked or has a written reason.
- Submission confirmed before 6:00 PM IST.

## 7. Testing requirements

- Full automated suite plus `network` tests, with real results reported.
- Manual: fresh clone setup; external client run against the public endpoint; video link opened in a private window.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-17: Release hardening, final results and submission`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `chore/SA-17-release-submission`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-17: Release hardening, final results and submission` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

None. If time remains before the deadline, do not start new work; re-read the README instead.
