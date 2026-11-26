# SA-16: README, results write-up and documentation

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P0 |
| Type | documentation |
| Estimate | 2h |
| Depends on | SA-12 (a generated report); SA-11 for the public URL |
| Components | `README.md`, `docs/RESULTS.md`, `docs/02-architecture.md`, environment reference |
| Branch | `docs/SA-16-readme-results` |
| Can run in parallel with | SA-15 |

## 1. Objective

Write the documentation the judges will read: a README structured around the judging criteria and both tracks, a results write-up, and accurate setup instructions.

## 2. Context

Submissions are a repository, a README and a video. Judges may use AI assistance to review, so the README must state plainly, under recognisable headings, how each criterion is met, with evidence and links. It must also be honest about limits. Documentation describes what was built, not what was planned: read the code and the latest report before writing.

## 3. Scope

**In scope**

- Full `README.md`.
- `docs/RESULTS.md` with screenshots.
- Update `docs/02-architecture.md` to match the implementation.
- Environment variable reference and testing instructions.

**Out of scope**

- The demo video.
- Marketing claims that the data does not support.

## 4. Implementation instructions

- **README structure:**
  1. Name, one-line description, badges (CI, licence), link to the video.
  2. The idea in three short paragraphs: a product has two audiences; the agent-facing interface is never measured; Second Audience measures it with agents as subjects.
  3. "What we tested" (Track 01): the question, hypothesis, the two arms with real payload examples, the metric, a results table, and the decision. Link to `HYPOTHESIS.md` and `docs/RESULTS.md`.
  4. "The operator agent" (Track 02): each `sa` command with a real output excerpt; the two approval points; what is automated and what a human decides.
  5. "Try it with your own agent": the public URL, connection snippets, the suggested instruction, and what is logged.
  6. Four sections titled exactly: "Use of Flagsmith", "Impact", "Creativity", "Demo video". Each gives specific evidence with links to code or screenshots.
  7. Architecture diagram (Mermaid) and a short component list.
  8. "Use it on your own MCP server": the minimal integration using `second_audience.core`, accurate to the code. If SA-18 was not built, describe what exists honestly and mark the rest as future work.
  9. Local setup, environment variables, running tests.
  10. Limitations, written plainly: lab population from one provider; a reference server, not a production system; sample size and intervals; per-session identity; exploratory breakdowns; field sessions are few.
  11. Built during Toggle Wars, Nov 26-29, 2026; tools used, including AI coding tools; licence.
- **`docs/RESULTS.md`:** the generated report's content, edited for reading, plus screenshots stored in `docs/img/`: the flag, the experiment setup, the exposures panel, the results page. The developer takes the screenshots; the agent lists exactly which ones are needed and where each is referenced. State the data cut-off time.
- Take every number from `reports/<slug>/report.md` or the saved results JSON. If final numbers are not available yet, insert clearly marked placeholders listed at the top of the pull request, to be replaced in SA-17.
- Describe lab traffic as lab traffic wherever results are mentioned.
- Verify every command in the README by running it. Verify every link.
- Do not mention Konnect or any employer.

## 5. Dependencies

- Tickets: SA-12 (a generated report); SA-11 for the public URL
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- The README contains the four criteria headings verbatim and a section for each track.
- Every command in the README was run successfully on Windows PowerShell, or is marked as requiring credentials.
- Every number matches the report or results JSON, or is a listed placeholder.
- Limitations are present and specific.
- Architecture document matches the code (module names, tools, settings).
- All links resolve.

## 7. Testing requirements

- Manual: follow the local setup from a fresh clone.
- Manual: a link check and a read-through for unsupported claims.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-16: README, results write-up and documentation`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P0` and the type label.
2. Branch from an up-to-date `main`: `docs/SA-16-readme-results`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-16: README, results write-up and documentation` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-17.
