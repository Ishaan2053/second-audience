# SA-22: Add-on: Slack, GitHub and audit-log integrations

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P3 |
| Type | infrastructure |
| Estimate | 1h |
| Depends on | SA-09; SA-11 for the webhook endpoint |
| Components | Flagsmith integration settings, `booking/server.py` (one route), `docs/RESULTS.md` |
| Branch | `chore/SA-22-flagsmith-integrations` |
| Can run in parallel with | Any |

## 1. Objective

Connect three Flagsmith integrations that make the operator's actions visible and independently verifiable: Slack notifications for flag changes, the GitHub link between the flag and the hypothesis pull request, and an audit-log webhook into the ledger.

## 2. Context

Track 02 values a human in the approval path and clear output. These integrations add evidence around that: when `sa start` or `sa ship` changes a flag, a Slack message appears; the flag is linked to the GitHub issue that pre-registered the hypothesis; and Flagsmith's own audit log confirms the operator's audit rows. Two of the three are configuration only. Whether each is available on the free plan is not stated on Flagsmith's integrations page, so check before spending time.

## 3. Scope

**In scope**

- Slack integration to a channel in the developer's personal workspace.
- GitHub integration linking `error_style` to the SA-09 issue or pull request.
- Audit-log webhook to `POST /hooks/flagsmith-audit`, stored in `audit_log` with action `flagsmith_audit`.
- Screenshots for the README and video.

**Out of scope**

- Analytics integrations (Amplitude, Mixpanel, Segment and similar).
- Observability integrations (Datadog, Grafana, Sentry and similar).
- Acting on webhook content: it is stored and displayed only.

## 4. Implementation instructions

- For each integration, first confirm in the Flagsmith dashboard that it can be enabled on the free plan. Skip any that cannot, and note it.
- **Slack and GitHub:** follow Flagsmith's integration documentation (check Context7). Developer performs the OAuth steps. Capture a screenshot of a Slack notification produced by a real operator action in `development`, and of the flag shown on the GitHub issue.
- **Audit-log webhook:** add one route that accepts POST JSON, verifies a shared secret (use the signature or secret mechanism Flagsmith documents for webhooks; if none is available, use an unguessable path segment from a new setting `SA_WEBHOOK_TOKEN`), caps body size, stores a trimmed record in `audit_log`, and returns 200 quickly. Treat the payload as untrusted data: never execute or interpolate it.
- `sa report` gains a short "Independent audit trail" table pairing each operator action with the matching Flagsmith audit event by time, where one exists.
- This ticket adds a route to `server.py` but must not alter tools, schemas or error payloads.

## 5. Dependencies

- Tickets: SA-09; SA-11 for the webhook endpoint
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- Each integration is either working with a screenshot as evidence, or recorded as unavailable on the free plan.
- The webhook route rejects requests without the correct secret and oversized bodies.
- A flag change in `development` produces an `audit_log` row through the webhook.
- No frozen experiment file changed.

## 7. Testing requirements

- Unit tests for the webhook route: valid, wrong secret, oversized, malformed JSON.
- Manual: trigger a flag change and observe Slack, GitHub and the ledger.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-22: Add-on: Slack, GitHub and audit-log integrations`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P3` and the type label.
2. Branch from an up-to-date `main`: `chore/SA-22-flagsmith-integrations`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-22: Add-on: Slack, GitHub and audit-log integrations` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

Return to the main sequence.
