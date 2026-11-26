# Spike Findings

Surprises found while building, with evidence. The Flagsmith questions are answered in SA-02.

## Tooling and CI (SA-01)

- **`astral-sh/setup-uv` has no moving major tag.** `uses: astral-sh/setup-uv@v10` fails with
  "Unable to resolve action `astral-sh/setup-uv@v10`, unable to find version `v10`". Only full
  versions exist (`v10.4.0` at the time of writing), so CI pins the exact version.
  `actions/checkout@v7` and `gitleaks/gitleaks-action@v3` do publish major tags.
- **Ruff 0.17 formats Python code blocks inside Markdown.** `ruff format --check .` covers
  `docs/`, so code samples in tickets and docs must be ruff-formatted.
- **pydantic-settings parses `list[str]` env values as JSON by default.** `SA_LAB_MODELS` uses
  `Annotated[list[str], NoDecode]` with a `before` validator to accept comma-separated IDs.
- **pydantic validation errors echo their input by default**, which would print any secrets
  already set. `Settings` sets `hide_input_in_errors=True`.

## Ledger (SA-05)

- **SQLAlchemy 2.1 defaults `postgresql://` to psycopg 3**, not psycopg2. The project depends
  on `psycopg[binary]` (bundles libpq, so it installs on Windows without a local Postgres)
  and documents `postgresql+psycopg://` URLs explicitly.
- **SQLite drops the UTC offset on read** even with `DateTime(timezone=True)`. The ledger
  uses a small `UtcDateTime` type that re-attaches UTC, so both backends return aware UTC
  timestamps.
- **The hosted ledger is Supabase Postgres (free plan).** On 2026-11-26 the `network` ledger
  suite passed against it (22 passed, about 200 s, mostly round-trip latency from creating a
  schema per test).
  - **IPv6 only on the direct host.** The direct host `db.<ref>.supabase.co:5432` is
    IPv6-only on the free plan. It worked from the developer's network, but an IPv4-only
    host will not reach it. For the public deployment (SA-11), use the **session pooler**
    URI from the dashboard's Connect dialog (`aws-<n>-<region>.pooler.supabase.com:5432`,
    user `postgres.<ref>`) with `?sslmode=require`.
  - **Not the transaction pooler.** Port 6543 does not support prepared statements, which
    psycopg 3 uses automatically.
  - **Keep the Data API off.** The ledger creates its tables in `public`, which Supabase
    can expose over REST.
  - **Weekly pause.** Free projects pause after a week of inactivity.

## Experiment client (SA-06)

- **A new variant takes a few seconds to reach every edge node.** Straight after
  `flagsmith feature variant add`, identity evaluations returned the flag as
  `reason=STATIC` with `variant=None`. For the next 3 to 6 seconds, answers were mixed: the
  same identity got `variant=None` from one request and a variant key from the next. After
  that, every identity was stable (traced over 30 s). Once the split is live, control
  identities carry `variant="control"`, so `variant is not None` is the signal. A single good
  probe is not enough: the first version of the network test waited for one, and a later
  run saw 17 control to 3 prescriptive with identities changing arm between clients. The test
  now waits for 10 consecutive probes with a variant key (5 to 14 s in practice).
- **With a `default_flag_handler`, SDK failures do not raise.** `get_identity_flags` catches
  `FlagsmithAPIError` and returns the handler's `DefaultFlag`, and a missing flag returns it
  too. The client treats any `DefaultFlag` as "not in the experiment", as well as raised
  exceptions.
- **The SDK retries failed connections three times by default**
  (`Retry(total=3, backoff_factor=0.1)`), so one unreachable-host call cost four timeouts.
  The client passes `Retry(total=1)`; against a closed local port with a 1 s timeout,
  `style_for` returned the control style in 2.0 s. urllib3 does not retry read timeouts on
  `POST` (the identities endpoint) by default.
- **`request_timeout_seconds` is typed `int`**, so the setting is an integer.

## MCP server and Checkpoint A (SA-07)

- **FastMCP 4.2.6 and `mcp` 2.3.0.** Both were current when added. `mcp` 2.x has a high-level
  `mcp.Client` that takes a URL, a `StdioServerParameters` or a `Transport`. To send custom
  headers, pass `streamable_http_client(url, http_client=create_mcp_http_client(headers=...))`.
  Tool listings use `input_schema`; `inputSchema` still works but warns.
- **Request headers inside tools work.** `fastmcp.server.dependencies.get_http_headers()`
  returns lower-case headers inside sync tools (which FastMCP runs in a thread pool), and `{}`
  over stdio. The MCP client's name is `ctx.session.client_params.client_info.name`. No
  `begin_task` fallback arguments are needed.
- **In-process HTTP tests.** `fastmcp.utilities.tests.asgi_client(server, headers=...)` runs
  the real Starlette app and lifespan with no socket, so tests cover the HTTP path, headers
  included.
- **The FastMCP banner checks PyPI for updates on every start** (`GET
  pypi.org/pypi/fastmcp/json`). The server passes `show_banner=False`, which skips both.
- **`strict_input_validation=True`** makes FastMCP validate arguments against the JSON schema
  exactly. Without it, pydantic's lax mode accepts `"5"` for an integer.
- **The local `.env` holds the Production server key.** Checkpoint A must run on Development;
  the first attempts silently evaluated Production (see `docs/checkpoint-a.md`). Anything run
  against Development must override `FLAGSMITH_ENVIRONMENT_KEY` explicitly. An empty override
  does not work: `env_ignore_empty=True` falls back to `.env`.
- **Flagsmith cleanup order.** A running experiment cannot be deleted (400), and its metrics
  cannot be deleted while it is active (409). Deleting the flag (`flagsmith feature delete`)
  removes its experiment too; the metrics can then be deleted.
- **PowerShell 5.1 `ConvertFrom-Json`** passes a JSON array down the pipeline as one object,
  so `| Where-Object` does not filter its items. Wrap it in parentheses first:
  `(... | ConvertFrom-Json) | Where-Object ...`.

## Lab runner (SA-08)

- **`gemini-2.5-flash-lite` is gone for this project.** On 2026-11-26 it returned `404
  NOT_FOUND`: "This model models/gemini-2.5-flash-lite is no longer available to new users.
  Please update your code to use models/gemini-3.5-flash-lite". The lab models are
  `gemini-3.1-flash-lite` and `gemini-3.5-flash-lite`; both completed live sessions.
- **Exact RPM and RPD are still unknown** (the AI Studio page needs a sign-in). The runner
  paces at `SA_LAB_REQUESTS_PER_MINUTE=8` and stops at `SA_LAB_REQUESTS_PER_DAY=400` per
  model, both settings. No per-minute or per-day 429 was seen in the pilot, so neither
  limit has been reached yet.
- **Gemini answers 503 and 504 under load.** `503 UNAVAILABLE` ("This model is currently
  experiencing high demand") and `504 DEADLINE_EXCEEDED` ("Deadline expired before operation
  could complete") came back on `gemini-3.1-flash-lite`. Server errors (500, 502, 503, 504)
  are retried with backoff like a per-minute 429. Four pilot runs failed on them before that
  change (two 503 before 503 was retried, two 504 before 5xx in general was).
- **google-genai 3.6.1 does not retry by default.** With no `HttpOptions.retry_options`,
  `retry_args(None)` stops after one attempt, so the runner's own pacing and backoff decide
  every request. The SDK's retry, when enabled, retries 429 and 503 alike.
- **No schema keyword was stripped.** Gemini accepted the booking tools' JSON Schema as-is
  through `parameters_json_schema`. The tools offered to the model use `type`, `properties`,
  `required`, `additionalProperties`, `description`, `enum`, `pattern`, `minLength`,
  `maxLength`, `minimum` and `maximum`. `GEMINI_UNSUPPORTED_KEYWORDS` is empty, so both
  arms keep the full strict schema.
- **Thought signatures.** Gemini 3 models return `thought_signature` on function-call parts.
  The client replays the model's own `Content` unchanged instead of rebuilding it, and sends
  the results of parallel calls together in one `user` content.
- **The HTTP logger is `httpx2`.** The MCP client and the Gemini SDK log each request at
  INFO through `httpx2`, not `httpx`; the runner quiets both.
- **Ctrl+C from the agent shell on Windows.** Processes started from the Claude Code tool
  shell inherit "ignore Ctrl+C", and Git Bash signals do not reach native Python. The resume
  demonstration started the runner in its own console with Ctrl+C re-enabled
  (`SetConsoleCtrlHandler(None, False)`) and sent `CTRL_C_EVENT` to that console. From a
  normal PowerShell window, Ctrl+C works directly.

### Pilot (2026-11-26)

Local server on the Flagsmith `Development` environment, which has no `error_style` flag
yet (SA-09 creates it), so every session got the control (informative) style outside any
experiment. Dedicated SQLite ledger, `plan --replicates 2` then `plan --replicates 1`
(36 runs per model), concurrency 1 then 2. Numbers are from the pilot ledger.

| Model | Sessions | Completion | Exposure | Model calls per session | Failed runs |
|---|---|---|---|---|---|
| `gemini-3.1-flash-lite` | 32 | 97% (31) | 19% (6) | 5.06 (max 8) | 4 (two 503, two 504) |
| `gemini-3.5-flash-lite` | 36 | 100% (36) | 28% (10) | 4.86 (max 9) | 0 |
| Both | 68 | 99% | 24% | 4.96 | 4 |

- **Exposure is far below the ~60% the plan assumed.** 70 of 76 sessions opened with
  `list_rooms`, and the models checked availability before booking, so they rarely hit a
  conflict. Exposure by scenario: s07 6/6, s08 5/5, s04 3/6, s01 1/5, s11 1/6, every other
  scenario 0. Only s07 and s11 of the five trap scenarios exposed anyone, so the guardrail
  `task_wrong_booking` would get almost no data. **SA-09 must revisit the scenarios before
  freezing them.**
- Requests per quota-day: about 5 requests per session, so the 400-request budget allows
  about 80 sessions per model per day, or about 19 exposed sessions per model at the pilot's
  exposure rate. The real free-tier RPD may allow more.
- 23 requests got a 503 or 504 and were retried after backoff; none was a per-minute or
  per-day 429.
- Interrupting runs left 10 orphan sessions, closed by the server as `abandoned`; their runs
  were replayed.

## Operator (SA-09)

- **The npm-installed `flagsmith` CLI always exits 0.** It is a Node wrapper (`index.js`) that
  spawns the native binary and never passes on its exit code. On a 404 or 400 the wrapper
  exits 0 while the native `flagsmith.exe` exits 1. Errors are printed to stderr prefixed with
  `Error:`, status lines (`✓ Created ...`) also go to stderr, and JSON goes to stdout.
  `FlagsmithAdmin` treats a non-zero exit **or** an `Error:` line on stderr as a failure, which
  works with either install.
- **`flagsmith feature get <missing>` is an error**, not an empty result, so lookups use
  `feature list --json` and filter by name. Each listed feature carries `default_value` and
  `variants: [{id, key, value, weight}]`.
- **Experiment responses nest the feature.** `POST .../experiments/` takes `"feature": <id>`,
  but list and detail responses return `"feature": {"id", "name", "initial_value",
  "multivariate_options", ...}`. Matching on the raw field missed the running experiment on
  the second `sa start`, which then offered to create it again (caught live on Development
  and fixed; `feature_id_of` handles both).
- **Create, then start.** As in Flagsmith's coding-agent guide: create the flag with the control
  value, add each keyed variant with `--weight 0`, create the experiment with
  `experiment_rollout.multivariate_feature_state_values` (control takes the remainder), then
  `POST .../experiments/<id>/start/`. Status goes `created` to `running`.
- **Metric aggregations** (`OPTIONS .../experiment-metrics/`): `count`, `sum`, `mean`,
  `occurrence`; directions `up`, `down`, `informational`. The tool-call metric uses `mean`.
- **Server keys are listable.** `GET api/v1/environments/<client key>/api-keys/` returns the
  environment's server keys, so `sa doctor` checks in-process that `FLAGSMITH_ENVIRONMENT_KEY`
  belongs to the spec's environment (the Checkpoint A pitfall) without printing either key.
- **The FastMCP server answers `ping` with "Method not found".** `sa doctor` lists the tools
  instead and requires `begin_task`.
- **The local `.env` held Production's client-side key**, not a server key (Checkpoint A
  described it as a server key). Both evaluate, but `sa doctor` requires a server key of the
  spec's environment, so Checkpoint B replaced it with Production's server key.
- **Development rehearsal (2026-11-26):** `sa doctor` passed all ten checks; `--dry-run` and a
  wrong confirmation left Flagsmith with no features or metrics; the typed slug created and
  started experiment 665; a second run reused every object and reported it as running.

## Status and report (SA-12)

- **A refused refresh is HTTP 429.** Inside the 5-minute window the CLI prints
  `{"detail":"A refresh was requested recently. Try again later. Expected available in 280
  seconds."}` and `POST .../results/refresh/ returned 429 Too Many Requests` on stderr.
  `sa report` matches "requested recently" and carries on with the latest computed results.
- **A `mean` metric has the same shape as `occurrence`.** For "Tool calls per session" the
  arm's `sum` is the total of `task_finished` values and `sum_squares` their squares (control
  `n=6, sum=26, sum_squares=114` on 2026-11-26). `sum / n` is the mean per exposed identity.
- **Inference stays `null` at small samples.** At 23 and 25 exposed identities per arm every
  metric's `inference.prescriptive` was `null`, and `srm_p_value` was `null` too, so the
  report's split check uses its own chi-square.
- **First live reconciliation was exact.** At 17:51 UTC on 2026-11-26 the ledger and
  Flagsmith agreed on every measure: exposures 23 and 25, tool-call sums 102 and 97,
  completions 23 and 23, wrong bookings 0 and 0.
- **Gemini 503 on a single request.** The first report with prose got `503 UNAVAILABLE`
  ("high demand") and fell back to the template as designed. The narrative client now enables
  the SDK's own retry (`HttpRetryOptions(attempts=3)`, which retries 408, 429 and 5xx);
  one report is one request, so no quota pacing is at stake.

## Flagsmith spike (SA-02)

Run on 2026-11-26 against the `Development` environment of project `second-audience`
(Flagsmith Cloud, free plan), with a throwaway flag `spike_style`, metrics `spike_converted`
(primary) and `spike_guardrail` (guardrail), and experiment 569. All of them were deleted
afterwards. Environment keys are shown as `<dev-key>` (client-side) and `<dev-server-key>`
(`ser.`). In `flagsmith api` paths, `$ENV` stands for `api/v1/environments/<dev-key>`.

Traffic came from `scripts/spike_flagsmith.py`. Even-numbered identities use the eager
exposure path, odd-numbered ones the lazy path, and each identity carries a `traffic` trait
(`lab` or `field`).

### Summary

| # | Question | Answer |
|---|---|---|
| 1 | SDK API | `flagsmith` 6.7.0: `get_experiment_flag`, `track_exposure_event`, `track_event`, `flush_events`; `enable_events=True` |
| 2 | Key type | Both work for remote evaluation and events. Use the server key (`ser.`) on the server |
| 3 | JSON as a value | Works. A 3,003-character JSON variant was stored and served intact; no limit hit |
| 4 | Latency | About 19 s from the first event to non-empty results; in practice set by the 5-minute refresh limit |
| 5 | Results JSON | Per metric: `variants.<key>.{n,sum,sum_squares}` and `inference.<variant>.{lift,ci_low,ci_high,chance_to_win}` |
| 6 | Several metrics | Yes. Each metric has an `expected_direction`; a guardrail is a metric with `not_increase` |
| 7 | Segment by trait | No. Results ignore query filters and have no breakdown. Use the ledger |
| 8 | End and ship | `POST .../experiments/<id>/complete/` with `rollout_decision` = `control`, `variant` or `disable` |
| 9 | Free plan | 50,000 API requests per month, 1 seat, 1 project, unlimited environments and identities. Experiments work on Free |
| 10 | Gemini quotas | Exact RPM and RPD not determined (the page needs a login). Reset time and recommended models below |

### 1. Python SDK

Verified against the installed package (`inspect.signature`, `inspect.getsource`) and the
live run.

```text
flagsmith 6.7.0
get_experiment_flag (self, feature_name: str, identifier: str, traits=None) -> DefaultFlag | Flag
track_exposure_event (self, feature_name: str, identifier=None, value=None, traits=None, metadata=None) -> None
track_event (self, event: str, identifier=None, value=None, traits=None, metadata=None) -> None
get_identity_flags (self, identifier: str, traits=None, *, transient: bool = False) -> Flags
close (self, timeout_seconds: float = 5) -> None
flush MISSING
```

- **Enable events:** `Flagsmith(environment_key=..., enable_events=True)`. The optional
  `event_processor_config=EventProcessorConfig(events_api_url, max_buffer_items=1000,
  flush_interval_seconds=10.0)` controls buffering.
- **Resolve with exposure:** `get_experiment_flag(feature_name, identifier=...)`. It raises
  `ValueError` if events are off. It sends the exposure only if the flag exists, is enabled,
  `flag.experiment.in_experiment` is true and `flag.variant` is set; otherwise it logs at
  debug level and skips. The exposure's `value` is the variant key, and its `metadata` is
  `{"experiment_id": ...}`.
- **Resolve without exposure:** `get_identity_flags(identifier).get_flag(feature_name)`.
  Record the exposure later with `track_exposure_event(feature_name, identifier=...,
  value=flag.variant, metadata={"experiment_id": flag.experiment.id})`, which is what
  `get_experiment_flag` does internally. The spike used this path for half of the
  identities, and every one of them was counted (see question 5).
- **Track an event:** `track_event(event, identifier=...)`. Names are case-sensitive and
  must match the metric's event name. Events only count after the identity's first exposure.
- **Flush:** `flush_events()` blocks until buffered events are delivered, dropped or kept
  for retry. There is no `flush()`. `close()` stops background work. Flagsmith's own setup
  snippet warns that buffered events are lost when the interpreter exits, so the server
  and the lab runner must call `flush_events()` before shutting down.
- **`Flag` fields:** `value`, `variant` (the variant key, `"control"` for control),
  `reason`, and `experiment: ExperimentMetadata(id, name, in_experiment) | None`.
  Observed reasons: `TARGETING_MATCH; segment=experiment-569-rollout` for control and
  `SPLIT; weight=50.0` for the variant.

### 2. Environment key type

The SDK checks for the `ser.` prefix only when `enable_local_evaluation=True`. Both keys
worked live for remote evaluation and events:

```text
== server key (ser.)
run_id=715c7d6b identities=40 sent_at=13:40:35
exposures={'control': 18, 'prescriptive': 22}
conversions={'prescriptive': 11, 'control': 3}
guardrails={'prescriptive': 1, 'control': 2}
== client key
run_id=5b14150b identities=4 sent_at=13:40:38
exposures={'prescriptive': 4}
conversions={'prescriptive': 3}
guardrails={'prescriptive': 1}
```

**Use the server key** (`ser.`) for `FLAGSMITH_ENVIRONMENT_KEY`. It is meant to stay
secret, and it allows local evaluation later without a key change. The client-side key is
public by design. The `flagsmith` CLI takes the **client-side** key as `--environment` /
`FLAGSMITH_ENVIRONMENT`, and Admin API paths address the environment by that same key; the
numeric environment id returned 403:

```text
GET api/v1/environments/120771/warehouse-connections/
{"detail":"You do not have permission to perform this action."}  (403 Forbidden)
```

### 3. JSON as a multivariate value

The control value was an 81-character JSON string; the variant was a 3,003-character JSON
string. Both were accepted by `flagsmith feature create` and `flagsmith feature variant
add`, stored as `type: "unicode"`, and returned intact by the SDK as `str`:

```text
sample value type=str length=81     (control)
sample value type=str length=3003   (variant)
```

No length limit was hit at 3,003 characters. The SDK returns the raw string, so the server
must call `json.loads` on it.

### 4. Event-to-results latency

```text
08:10:21Z  experiment 569 started
08:10:30Z  first identity resolved (the run of 40 took 4.6 s)
08:10:46Z  GET $ENV/warehouse-connections/
           status=pending_connection total_events_received=65 last_event_received_at=08:10:37Z
08:10:49Z  POST $ENV/experiments/569/results/refresh/
           results as_of 2026-11-26T08:10:49.621940Z, non-empty
```

The warehouse counted exactly what was sent: 44 exposures plus 21 events = 65, from 3
distinct event names. Results were **non-empty about 19 seconds after the first event**.
In practice latency is set by refreshes: `POST .../results/refresh/` is limited to one every
5 minutes (`RESULTS_REFRESH_MIN_INTERVAL` in the Flagsmith source). A refresh inside the
window is rejected:

```text
{"detail":"A refresh was requested recently. Try again later. Expected available in 250 seconds."}
```

A refresh outside the window returned `HTTP/2.0 202 Accepted`, and the new results were
ready within about a second. The warehouse status stayed `pending_connection` throughout,
even while it received and served events.

### 5. Results JSON

`GET $ENV/experiments/569/results/` after 204 identities (trimmed to the primary metric):

```json
{
  "results": {
    "as_of": "2026-11-26T08:16:59.877545Z",
    "last_error_at": null,
    "refresh_requested_at": "2026-11-26T08:16:59.829391Z",
    "is_final": false,
    "payload": {
      "srm_p_value": null,
      "exposures_timeseries": {
        "granularity": "hour",
        "points": [
          {
            "bucket": "2026-11-26T08:00:00+00:00",
            "new_identities": {"control": 98, "prescriptive": 106}
          }
        ]
      },
      "metrics": [
        {
          "metric_id": 563,
          "variants": {
            "control": {"n": 98, "sum": 28.0, "sum_squares": 28.0},
            "prescriptive": {"n": 106, "sum": 62.0, "sum_squares": 62.0}
          },
          "inference": {
            "prescriptive": {
              "lift": 1.0471698113207548,
              "ci_low": 0.3234715466277842,
              "ci_high": 1.7708680760137254,
              "chance_to_win": 0.9977159452662091
            }
          },
          "conversions_timeseries": {
            "granularity": "hour",
            "points": [
              {
                "bucket": "2026-11-26T08:00:00+00:00",
                "converted_identities": {"control": 28, "prescriptive": 62}
              }
            ]
          }
        }
      ]
    }
  }
}
```

The guardrail metric (564) in the same payload:

```text
variants:  control n=98 sum=14.0   prescriptive n=106 sum=6.0
inference: lift=-0.6037735849056604 ci_low=-0.9685110037564464
           ci_high=-0.23903616605487432 chance_to_win=0.9994116238735113
```

Field meanings, checked against the counts the script printed:

- **Exposures per variation:** `metrics[].variants.<key>.n`, also
  `exposures_timeseries.points[].new_identities`. Control 98 = 18 + 80, prescriptive
  106 = 22 + 4 + 80. These match the script exactly, across both exposure paths and both
  key types.
- **Conversions:** `variants.<key>.sum` for an `occurrence` metric. Control 28 = 3 + 25,
  prescriptive 62 = 11 + 3 + 48.
- **Lift:** `inference.<variant>.lift`, relative to control:
  (62 / 106) / (28 / 98) - 1 = 1.047.
- **95% credible interval:** `inference.<variant>.ci_low` and `ci_high`, on the lift.
- **Win probability:** `inference.<variant>.chance_to_win`. It is direction-aware: for the
  `not_increase` guardrail, a lift of -0.604 gave a `chance_to_win` of 0.9994.
- **Before thresholds are met:** `results` is `null` until the first refresh, and
  `inference.<variant>` is `null` (it was `null` at 18 and 26 identities). The Flagsmith
  source (`api/experimentation/constants.py`, `services.py`) shows that inference needs **at
  least 50 identities and at least 5 conversions per variant, for each metric**.
  `srm_p_value` needs at least 100 identities in total; it was still `null` at 204, for
  reasons not determined.

### 6. Several metrics and guardrails

`OPTIONS $ENV/experiments/` shows `metrics` as a list of `{metric, expected_direction}`,
where `expected_direction` is `increase`, `decrease`, `not_increase` or `not_decrease`. The
experiment was created with two metrics:

```json
"metrics": [
  {"metric": 563, "expected_direction": "increase"},
  {"metric": 564, "expected_direction": "not_increase"}
]
```

A guardrail is not a separate type. It is a metric whose `expected_direction` is
`not_increase` (or `not_decrease`), and it appears as its own entry in
`results.payload.metrics`. Metrics themselves were created with:

```text
POST $ENV/experiment-metrics/
{"name":"spike_guardrail","aggregation":"occurrence","direction":"down","definition":{"version":1,"event":"spike_guardrail"}}
-> {"id":564, ..., "experiments":[]}
```

### 7. Segmenting results by trait

Not supported. The results payload has no breakdown dimension, and query parameters are
ignored:

```text
results/?traffic=lab         control n per metric [98, 98]
results/?trait=traffic:lab   control n per metric [98, 98]
results/?segment=traffic     control n per metric [98, 98]
```

An experiment can be limited to one segment up front
(`experiment_rollout.audience.segment_ids`, at most one), but that restricts enrolment and
does not split results. Lab-versus-field and per-model breakdowns therefore come from the
ledger, as the architecture already plans.

### 8. Ending an experiment and setting the winner

From the API (`OPTIONS` and 400 responses) and the Flagsmith source
(`api/experimentation/models.py`, `serializers.py`):

- `POST $ENV/experiments/<id>/complete/` with `{"rollout_decision": ...}` ends the
  experiment. The choices are `control`, `variant` (which needs
  `"rollout_variant": <multivariate option id>`) and `disable`.
- `PATCH $ENV/experiments/<id>/rollout/` edits a running experiment's rollout percentage and
  split. It does not pick a winner. `POST` to it returns 405.
- The experiment list's `status_counts` includes `paused`, so a pause state exists. It was
  not tested.

Live, ending the experiment and rolling out the variant in one call:

```text
POST $ENV/experiments/569/complete/  {"rollout_decision":"variant","rollout_variant":37070}
{'id': 569, 'status': 'completed', 'ended_at': '2026-11-26T08:18:34.721452Z',
 'rollout_decision': 'variant', 'rollout_variant': 37070}
```

New identities then received the winning value, outside any experiment:

```text
0 style= {"style": "prescriptive", "mes variant= prescriptive reason= SPLIT; weight=100.0 exp= None
```

Completing is irreversible: an ended experiment cannot be restarted. Straight after
completion, `results.is_final` was still `false` with the earlier `as_of`, so a final
refresh is probably needed to freeze the results. This was not confirmed before cleanup.

### 9. Free-plan limits

From `GET api/v1/organisations/` on the account:

```text
"plan": "free", "max_seats": 1, "max_api_calls": 50000
```

From flagsmith.com/pricing (read 2026-11-26): up to 50,000 requests per month, up to
100,000 users in experiments, 1 team member, 1 project, unlimited environments, and
unlimited identities and segments ("Fair usage policy applies"). On Free, an overage gives
7 days to upgrade before any action is taken. The pricing page lists "A/B and MVT Testing"
only under Start-Up, but experiments, metrics, the managed warehouse and results all worked
on this free organisation.

**Budget:** each remote evaluation is one API request. The spike used about 210. Field
traffic must stay well under 50,000 per month, which SA-10's rate limits protect.

### 10. Gemini free tier

**Could not determine the exact RPM and RPD.** What was tried:

- `ai.google.dev/gemini-api/docs/rate-limits` redirects to
  `aistudio.google.com/docs/rate-limits`. That page has no per-model free-tier table; it
  links to `aistudio.google.com/rate-limit`, which needs a Google sign-in.
- The developer checked that page while signed in and reported more than 50% of the quota
  remaining on Gemini 3.1 Flash-Lite and Gemini 2.5 Flash-Lite, without the numbers.

**Reset time:** "Requests per day (RPD) quotas reset at midnight Pacific time." Limits apply
per project, not per API key. Midnight Pacific is **13:30 IST** while Pacific Standard Time
applies, which covers the whole hackathon window.

Stable text models listed on `aistudio.google.com/docs/models`: `gemini-3.8-flash`,
`gemini-3.6-flash`, `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite`. `gemini-2.5-flash`
and `gemini-2.5-pro` start auto-routing to newer models on 2026-12-01, after the hackathon.

## Decisions for later tickets

- **SA-06: lazy exposure.** Call `get_experiment_flag(flag, identifier=session_id)` at the
  first styled error; that one call resolves the variant and records the exposure. The
  separate `get_identity_flags` + `track_exposure_event` path also works (verified), but is
  only needed if the variant must be known before it is shown. Call `track_event` only for
  exposed sessions, and `flush_events()` after each `finish_task` and on shutdown. Parse
  the flag value with `json.loads`. Treat a `Flag` with `experiment is None` or no `variant`
  as not in the experiment.
- **SA-06 and SA-07 key:** `FLAGSMITH_ENVIRONMENT_KEY` is the server key (`ser.`).
- **SA-08: models and volume.** Use `gemini-3.1-flash-lite` and `gemini-2.5-flash-lite`,
  which both have confirmed free quota on the developer's project, with
  `gemini-3.5-flash-lite` as a third if quota allows. (SA-08: `gemini-2.5-flash-lite` is no
  longer available; the lab uses `gemini-3.1-flash-lite` and `gemini-3.5-flash-lite`.) SA-08 must read the exact RPM and RPD
  from the AI Studio rate-limit page before sizing the run. Flagsmith shows no inference
  until each arm has at least 50 exposed sessions and at least 5 conversions **per metric**,
  so the guardrail `task_wrong_booking` needs at least 5 wrong bookings per arm; trap
  scenarios must be frequent enough to produce them. Plan for at least 60 exposed sessions
  per arm.
- **SA-09:** `doctor` should check that a warehouse connection exists, not that its status
  is `connected` (it stayed `pending_connection` while working). Create experiments with
  `metrics: [{metric, expected_direction}]` and `experiment_rollout` as in question 6, and
  respect the 5-minute refresh limit.
- **SA-14: ship.** After approval, one call:
  `POST $ENV/experiments/<id>/complete/` with
  `{"rollout_decision": "variant", "rollout_variant": <option id>}` to ship the
  prescriptive style, or `{"rollout_decision": "control"}` to keep the informative style.
  It is irreversible, so the approval prompt must say so. Afterwards, refresh once and
  confirm `is_final`.
- **SA-10 (field hardening), found while building it.**
  - FastMCP 4.2.6 mounts extra HTTP routes with `@mcp.custom_route(path, methods=[...])`
    on the same Starlette app as `/mcp`; they are served only over HTTP. `get_http_request()`
    raises `RuntimeError` under stdio, so the client IP is optional there and only the kill
    switch and the session cap apply.
  - Uvicorn's access log prints every client address, so `main` passes
    `uvicorn_config={"access_log": False, "proxy_headers": False}`. Forwarded headers are
    read only by `limits.client_ip`, under `SA_TRUST_PROXY`.
  - The Flagsmith SDK never raises on an API failure when a default flag handler is set: it
    serves the default. `/healthz` therefore treats a real `Flag` (not `DefaultFlag`) for
    `error_style` from `get_environment_flags()` as proof that Flagsmith answered.
  - **For SA-11:** the client IP is the leftmost `X-Forwarded-For` entry. That is the real
    client behind any number of proxy hops, but a client can prepend a fake value to dodge
    the per-IP limit; the global session cap still bounds the load. Check on the host that
    field sessions from two networks get two different `client_ip_hash` values in the ledger.

## Public deployment (SA-11)

- **Host terms changed in 2026.** Hugging Face Docker Spaces now need a paid plan (PRO) to
  create; only ZeroGPU Gradio Spaces stay free. Koyeb's free instance now needs a card with
  a $29 pre-authorisation. Render's free web service still needs no card, so it is the host
  (`docs/deployment.md`, section 1).
- **Render passes its variables to Docker builds as build args,** including the default
  `RENDER_GIT_COMMIT`. The Dockerfile declares `ARG SA_GIT_COMMIT=${RENDER_GIT_COMMIT}`, so
  `/healthz` shows the commit on Render with no extra configuration, and an explicit
  `--build-arg SA_GIT_COMMIT=...` still wins (checked locally with both).
- **Blueprint `sync: false` values belong to the Dashboard.** Render prompts for them only
  when the Blueprint is created and never overwrites them on sync, which is why the kill
  switch `SA_FIELD_ENABLED` is declared that way rather than with a `value:`.
- **750 free hours per workspace, not per service.** An always-awake service uses about 744
  in a 31-day month, so the keep-alive ping is safe only while this is the workspace's only
  free web service.
