# Flagsmith Add-ons and Pre-freeze Amendments

Researched Nov 26, 2026 from Flagsmith's integrations page, experimentation page, pricing
page and documentation. Plan availability of individual integrations on the free plan is
not stated there, so each add-on ticket checks it first.

## Part 1: Amendments to apply before Checkpoint B

These are not optional extras. They are small changes to existing tickets that become
impossible once the experiment starts, because the flag and experiment are locked.

### A1. Send identity traits (amends SA-06 and SA-07)

When the experiment client resolves the flag for a session, also set these traits on the
identity: `agent_label` (the model or client name), `source` (`lab` or `field`),
`scenario_kind` (`aligned` or `trap`).

- **Why:** traits make each identity meaningful in Flagsmith's dashboard, allow Flagsmith
  segments to target by model later (SA-21), and may allow results to be segmented in
  Flagsmith itself if SA-02 finds that is supported.
- **How:** `style_for(session_id, traits)` gains a `traits` argument. Use the SDK's
  documented way to pass traits with identity flag evaluation (check Context7). If passing
  traits interferes with the experiment-flag call, set them with a separate identify call
  immediately before it.
- **Cost:** about 20 minutes. Note that trait and identity calls count toward the free
  plan's 50,000 requests per month; a few hundred sessions is far below that.

### A2. Track an efficiency metric with a numeric value (amends SA-06, SA-07, SA-09)

Flagsmith metrics can aggregate an event's numeric value (total or average), not only
whether it occurred. Send one more event at finalisation for exposed sessions:
`task_finished` with value = the session's tool-call count. Add a third metric to the spec:
"Tool calls per session", average of `task_finished`, direction down.

- **Why:** one of the arguments for the prescriptive style is fewer round trips. Without
  this metric that claim is only visible in our own ledger; with it, Flagsmith's engine
  reports it with a credible interval.
- **How:** `track(session_id, event, value=None)`; allow `task_finished` in the known
  events; add the metric to `experiments/error-style-v1.yaml` and to `HYPOTHESIS.md` as a
  secondary metric. Confirm the aggregation name for an average-value metric in SA-02.
- **Cost:** about 30 minutes.

## Part 2: Optional add-on tickets

Build only if the main sequence is ahead of schedule. Order by value:

| Ticket | What | Why it helps | Est. |
|---|---|---|---|
| SA-21 | Per-model rollout with a segment override | Turns the per-model finding into an action: ship the winner only to the models it helped. Strongest demo addition. | 1.5h |
| SA-20 | Kill switch and limits as Flagsmith flags | Real operational value on a free host, and shows Flagsmith's core flag and remote-config features in use. | 1h |
| SA-22 | Slack, GitHub and audit-log webhook | Makes approvals visible and independently verified. Mostly configuration. | 1h |

## Part 3: Considered and rejected

| Feature | Why not |
|---|---|
| Amplitude, Mixpanel, Segment, Rudderstack, Heap | They exist to run A/B analysis outside Flagsmith. We use Flagsmith Experimentation directly, and each needs another account. |
| Datadog, Dynatrace, Grafana, Sentry, New Relic, AppDynamics | Observability for production systems; no judging value here and more accounts to manage. |
| Bring-your-own warehouse (Postgres, ClickHouse, Databricks) | The managed warehouse is zero-configuration and is on every plan. Our own warehouse adds risk on the critical path for no visible gain. |
| Scheduled flags, change requests | Listed for paid plans. |
| OpenFeature provider | Would make the middleware vendor-neutral, which dilutes the "use of Flagsmith" criterion; experiment events are also outside its scope. |
| Terraform provider | The operator already manages Flagsmith through the CLI and API. |
| Jira, GitLab, Backstage | Not part of this project's workflow. |
| Flagsmith MCP server | Cannot start experiments, per Flagsmith's own agent setup guide. |
