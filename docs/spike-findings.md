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
  `gemini-3.5-flash-lite` as a third if quota allows. SA-08 must read the exact RPM and RPD
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
