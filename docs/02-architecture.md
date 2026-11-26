# Technical Architecture

## 1. System overview

```mermaid
flowchart LR
    subgraph Subjects
        LAB[Lab runner<br/>Gemini models]
        FIELD[Field agents<br/>any MCP client]
    end
    subgraph Server["Reference MCP server (FastMCP)"]
        TOOLS[Booking tools]
        ERR[Error renderer<br/>style from flag]
        SESS[Session manager<br/>world sandbox + checker]
    end
    subgraph Core["second_audience.core"]
        EXP[ExperimentClient]
        LEDGER[(Ledger<br/>Postgres / SQLite)]
    end
    subgraph Flagsmith["Flagsmith Cloud"]
        FLAG[Multivariate flag<br/>error_style]
        WH[(Managed warehouse)]
        STATS[Bayesian results]
    end
    OP[Operator CLI: sa] -->|flagsmith CLI + Admin API| Flagsmith
    OP --> LEDGER
    HUMAN((Human)) -->|approve| OP
    LAB -->|MCP over HTTP| TOOLS
    FIELD -->|MCP over HTTP| TOOLS
    TOOLS --> SESS --> ERR
    ERR --> EXP
    SESS --> EXP
    EXP -->|get flag, exposure, events| FLAG
    EXP --> WH --> STATS
    SESS --> LEDGER
```

## 2. Repository layout

```
second-audience/
  CLAUDE.md  AGENTS.md  README.md  HYPOTHESIS.md  LICENSE
  pyproject.toml  uv.lock  .env.example  Dockerfile  .dockerignore  render.yaml
  .github/workflows/ci.yml  keepalive.yml
  experiments/error-style-v1.yaml
  reports/                      generated reports and saved results JSON
  docs/
  src/second_audience/
    config.py                   Settings (pydantic-settings)
    core/
      experiment.py             ExperimentClient (Flagsmith SDK wrapper)
      ledger.py                 Ledger (SQLAlchemy Core)
      styles.py                 ErrorStyle config parsed from the flag value
    booking/
      world.py                  rooms, bookings, fixed clock
      scenarios.py              scenario definitions
      checker.py                final-state correctness check
      errors.py                 state-dependent errors and rendering
      suggestions.py            suggestion heuristic
      sessions.py               session lifecycle and budgets
      server.py                 FastMCP app and tools
    lab/
      models.py                 ModelClient protocol, Gemini implementation
      plan.py                   run matrix
      runner.py                 agent loop, pacing, resume
    operator/
      cli.py                    Typer app: sa
      flagsmith_admin.py        CLI and Admin API wrapper
      spec.py                   experiment spec model
      report.py  propose.py  ship.py  gemini.py
  tests/
```

## 3. Components and contracts

### 3.1 Configuration (`config.py`)

One `Settings` class, validated at start-up. Missing required values fail fast with a clear message.

| Variable | Used by | Purpose |
|---|---|---|
| `SA_ENV` | all | `dev`, `test` or `prod` |
| `FLAGSMITH_ENVIRONMENT_KEY` | server | SDK key for the Flagsmith environment |
| `FLAGSMITH_API_URL` | server | optional override |
| `FLAGSMITH_REQUEST_TIMEOUT_SECONDS` | server | per-request timeout, default 2; one retry, then the control style |
| `SA_FLAG_NAME` | server, operator | default `error_style`; `sa doctor` checks the spec uses it |
| `SA_FLAGSMITH_PROJECT` | operator | Flagsmith project the CLI manages; default `second-audience` |
| `SA_DATABASE_URL` | server, operator, lab | ledger; default `sqlite:///./sa.db`; Postgres as `postgresql+psycopg://...` |
| `GEMINI_API_KEY` | lab, operator | model access |
| `SA_LAB_MODELS` | lab | comma-separated model IDs |
| `SA_LAB_KEY` | server, lab | shared secret marking lab sessions |
| `SA_SERVER_URL` | lab | MCP endpoint the runner targets |
| `SA_LAB_REQUESTS_PER_MINUTE` | lab | pacing per model, default 8 |
| `SA_LAB_REQUESTS_PER_DAY` | lab | per-model daily budget before parking, default 400 |
| `SA_LAB_CONCURRENCY` | lab | sessions at once, 1 to 3, default 1 |
| `SA_OPERATOR_MODEL` | operator | Gemini model that drafts the prose of `sa report` and the proposals of `sa propose`, default `gemini-3.5-flash-lite` |
| `SA_MAX_TOOL_CALLS` | server | per-session budget, default 12 |
| `SA_SESSION_IDLE_SECONDS` | server | default 600 |
| `SA_FIELD_SESSIONS_PER_IP_PER_HOUR` | server | new field sessions per client IP per sliding hour, default 10 |
| `SA_FIELD_ENABLED` | server | kill switch; `false` refuses new field sessions, lab sessions still work; default `true` |
| `SA_MAX_CONCURRENT_SESSIONS` | server | open sessions (lab and field) above which field `begin_task` is refused, default 50 |
| `SA_TRUST_PROXY` | server | take the client IP from `X-Forwarded-For`; `true` only behind the host's proxy; default `false` |
| `SA_GIT_COMMIT` | server | commit hash shown at `/healthz`, set by the host or the Docker build |

The Flagsmith admin credential is never stored in this project. The operator shells out to
the `flagsmith` CLI, which holds its own login.

### 3.2 Booking domain (`booking/`)

A fictional office with six rooms. Each room has an ID, capacity, features (projector,
whiteboard, video) and opening hours. The clock is fixed to one working week so that tasks
use explicit ISO dates and no date arithmetic is needed. Times are local, on 30-minute steps.

World state is created per session from the scenario's seed bookings and held in memory.
Sessions cannot see or affect each other.

| Room | Capacity | Features | Hours |
|---|---|---|---|
| R1 | 2 | none | 08:00-20:00 |
| R2 | 4 | whiteboard | 08:00-20:00 |
| R3 | 6 | projector, whiteboard | 08:00-20:00 |
| R4 | 8 | video | 08:00-18:00 |
| R5 | 10 | projector, video | 08:00-20:00 |
| R6 | 16 | projector, whiteboard, video | 08:00-20:00 |

The week is Monday 2027-03-15 to Friday 2027-03-19. World operations raise typed domain
errors carrying a `code` and a structured `details` dict, never user-facing text. A booking
is checked in this order: capacity, opening hours, conflict. Intervals are half-open, so
back-to-back bookings do not conflict and a booking may end exactly at closing time.
Malformed input (unknown room, date outside the week, off-step time, start not before
end, attendees below 1) raises `ValueError`; the MCP schema rejects it before it gets there.

**Tools (identical schema in both arms):**

| Tool | Arguments | Notes |
|---|---|---|
| `begin_task` | `scenario_id?` | Creates a session; returns `session_id`, the task brief and the call budget. |
| `list_rooms` | `session_id` | |
| `check_availability` | `session_id, room_id, date` | Free slots for that room and date. |
| `book_room` | `session_id, room_id, date, start, end, attendees, title` | |
| `reschedule_booking` | `session_id, booking_id, date, start, end` | |
| `cancel_booking` | `session_id, booking_id` | |
| `finish_task` | `session_id, summary` | Finalises and scores the session. Idempotent. |

Every tool takes `session_id` explicitly. **Why:** it works over any transport, does not
depend on a client preserving an MCP session header, and is trivial to test.
**Alternative considered:** binding to the MCP transport session; rejected because client
behaviour varies and the public endpoint must accept any client.

**Schema strictness.** Dates, times, enumerated room IDs and integer ranges are enforced by
the schema. Schema failures return the same plain validation message in both arms and are
not part of the experiment.

### 3.3 State-dependent errors and styles (`booking/errors.py`, `core/styles.py`)

Styled error codes: `ROOM_CONFLICT`, `CAPACITY_EXCEEDED`, `OUTSIDE_OPENING_HOURS`.
Unstyled (same in both arms): `BOOKING_NOT_FOUND`, `BOOKING_CANCELLED`, `SESSION_NOT_FOUND`,
`BUDGET_EXHAUSTED`, `INVALID_ARGUMENTS` (input the schema cannot express, such as a start
time after the end time).

The flag value is a JSON string parsed into `ErrorStyle`:

```json
{"suggest_alternative": false, "corrected_call": false, "caution_note": false}
```

| Arm | Flag value |
|---|---|
| `control` (informative) | all false |
| `prescriptive` | `suggest_alternative: true`, `corrected_call: true` |

`caution_note` exists so that a future challenger can be created from configuration alone,
without a redeploy. It is not used in experiment 1.

Informative payload:

```json
{"error": {"code": "ROOM_CONFLICT",
           "message": "Room R2 is already booked 14:00-15:00 on 2027-03-15."}}
```

Prescriptive payload adds:

```json
{"suggestion": {"room_id": "R2", "date": "2027-03-15", "start": "15:00", "end": "16:00",
                "reason": "nearest free slot of the same length"},
 "corrected_call": {"tool": "book_room", "arguments": {"...": "..."}}}
```

**Suggestion heuristic (fixed before the experiment starts).**

- Conflict: same room, nearest free slot of the same length on the same day (ties go later);
  if none, the smallest other room free at the requested time.
- Capacity: the smallest room with enough capacity free at the requested time; if none,
  that room's nearest free slot.
- Opening hours: the nearest in-hours slot in the same room.

The heuristic knows nothing about the user's time window or required features. That is
realistic, and it is what makes trap scenarios possible.

Details fixed in SA-04 (`booking/suggestions.py`):

- "Nearest" means the 30-minute-step start closest to the requested start; ties go later.
  Every suggested slot is free and inside opening hours, so applying it cannot hit the
  same error again.
- Rooms are compared by capacity, then by ID. "Other room" and "room with enough
  capacity" both require capacity for the call's attendee count.
- A `reschedule_booking` cannot change room, so it gets only same-room suggestions, and
  the booking being moved does not block its own suggestion.
- No alternative: the suggestion, `corrected_call` and `note` fields are all omitted.
- `corrected_call` repeats the attempted tool and its arguments (including `session_id`)
  with the suggested room, date, start and end applied. `corrected_call: true` without
  `suggest_alternative` is treated as false; `caution_note` appears only with a suggestion.
- Missing or invalid flag values parse to the control style and log a warning. Unknown
  keys are ignored. The canonical arm values are `ARM_FLAG_VALUES` in `core/styles.py`.
- `render_error` reports whether the error was styled, so exposure is recorded only for
  styled errors.

### 3.4 Scenarios and correctness (`scenarios.py`, `checker.py`)

Twelve scenarios, each built so the obvious first attempt hits a styled error.

- **Aligned (7):** the heuristic's suggestion satisfies the brief.
- **Trap (5):** the suggestion violates the brief (ends after a stated deadline, lacks a
  required feature, or is too small).

A scenario defines: `id`, `kind`, `brief`, seed bookings, and constraints (date, allowed
time window, duration, attendees, required features). It also records `first_attempt`, the
call the brief invites, and `solution`, one known correct booking. Neither is shown to the
agent; tests, SA-04 and the smoke script use them. IDs are neutral (`s01` to `s12`) so an ID
never reveals the kind.

| Styled error on first attempt | Aligned | Trap |
|---|---|---|
| `ROOM_CONFLICT` | s01, s03, s08 | s05 (suggestion ends after deadline), s11 (starts before earliest time) |
| `CAPACITY_EXCEEDED` | s06, s10 | s02 (suggested room lacks video), s09 (lacks projector) |
| `OUTSIDE_OPENING_HOURS` | s04, s12 | s07 (starts before earliest time) |

The checker scores room capacity against the brief's attendee count, not the attendee
number written on the booking.

**Outcome at finalisation:**

| Outcome | Meaning |
|---|---|
| `correct` | Exactly one active agent-made booking, and it satisfies every constraint. |
| `incorrect` | At least one active agent-made booking, and the state is not correct. |
| `no_booking` | No active agent-made booking. |
| `budget_exhausted` | Call budget reached before `finish_task`. The final state is still scored and stored as `final_state`, and events follow it. |
| `abandoned` | Idle timeout or server restart. |

Finalisation triggers: `finish_task`, budget exhaustion, or idle timeout.

### 3.4.1 Sessions and the server (`sessions.py`, `server.py`, fixed in SA-07)

- `SessionManager` holds each session's world in memory. Every domain call goes: validate the
  session, count the call, run the world operation, render a domain error in the session's
  style, record the call in the ledger, return the payload. The style is resolved, and the
  exposure recorded, at the session's first styled error; unstyled errors never resolve it.
- **Budget.** `begin_task` and `finish_task` do not count. With a budget of 12, the first 12
  domain calls run; the 13th finalises the session as `budget_exhausted` and returns
  `BUDGET_EXHAUSTED`, as does every later call. An agent that uses exactly 12 calls can still
  `finish_task` normally. Calls refused for the budget are not recorded.
- **Finalisation** is one idempotent function. It stores the outcome and `final_state` (the
  checker's result) once, and sends `task_completed` or `task_wrong_booking` from
  `final_state`, plus `task_finished` with the call count, for sessions exposed inside the
  experiment, except abandoned ones.
  `finish_task` returns `{session_id, finished, outcome}`; repeat calls return the same.
  It is recorded in the transcript (with the agent's summary) after the last domain call.
- **Idle expiry.** A background task runs every `min(60, SA_SESSION_IDLE_SECONDS)` seconds. It
  finalises open sessions idle for `SA_SESSION_IDLE_SECONDS` as `abandoned` and forgets every
  idle session, finished or not. Later calls get `SESSION_NOT_FOUND`. Stopping the server
  abandons every open session and flushes events.
- **Lab or field.** FastMCP's `get_http_headers()` works inside tools, so no fallback
  arguments are needed. A request with `X-SA-Lab-Key` equal to `SA_LAB_KEY` (compared with
  `hmac.compare_digest`) is `lab`, labelled by `X-SA-Agent-Label`; anything else is `field`,
  labelled by the MCP client's `clientInfo.name`. Missing labels are stored as `unknown`.
- **Schema.** `strict_input_validation=True`, so the JSON schema is enforced exactly (no
  string-to-integer coercion). `room_id`, `date` and `scenario_id` are enums, times match
  `HH:MM` on 30-minute steps, `attendees` is 1 to 20.
- **Responses never carry the variant key.** Domain errors are returned as normal results
  (the payload's `error` object), not MCP tool errors; schema violations are MCP tool errors.

### 3.5 Experiment client (`core/experiment.py`)

```python
class ExperimentClient(Protocol):
    def style_for(
        self, session_id: str, traits: Mapping[str, str] | None = None
    ) -> tuple[ErrorStyle, str | None]: ...
    def track(self, session_id: str, event: str, value: float | None = None) -> None: ...
    def flush(self) -> None: ...
```

- `style_for` is called the first time a session needs a styled error. One SDK call,
  `get_experiment_flag(flag, identifier=session_id)`, resolves the flag and records the
  exposure. The result is cached per session in a bounded LRU (4,096 sessions), so repeated
  calls neither re-evaluate nor re-expose. Returns the style and the variant key.
- **Identity traits (SA-09, amendment A1).** The same call carries `agent_label`, `source`
  (`lab` or `field`) and `scenario_kind` as traits, stored on the Flagsmith identity so a
  segment can target them later (SA-21). They ride on the evaluation request; no extra call.
- **Lazy exposure. Why:** a session that never hits a styled error is unaffected by the
  variant; counting it would dilute the result. Whether a session reaches its first styled
  error does not depend on the arm, so this keeps the comparison valid.
  **Alternative considered:** exposure at `begin_task`; simpler, but not needed: the SDK
  makes lazy exposure a single call (SA-02).
- The variant key is set only while the identity is enrolled in a running experiment. A
  flag outside an experiment (for example after `sa ship` rolls out a winner) still serves
  its style, with variant `None`.
- If Flagsmith fails (exception, timeout, or the SDK's default flag), the client returns
  the control style with variant `None`, logs once, and caches that for the session, so a
  session never switches style mid-task. The ledger marks it `in_experiment = false`.
  Requests time out after `FLAGSMITH_REQUEST_TIMEOUT_SECONDS` (default 2) with one retry,
  so an outage costs a tool call about two timeouts at most.
- Events: `task_completed` when the outcome is `correct`; `task_wrong_booking` when it is
  `incorrect`; and `task_finished` for every scored session, with the session's domain
  tool-call count as its numeric value (SA-09, amendment A2), for the "tool calls per session"
  metric (`mean` aggregation, the primary metric). The caller sends them only for sessions
  that were exposed (Flagsmith also ignores events from identities with no exposure). `track` never raises on delivery
  failures; an unknown event name raises `ValueError`. `flush` blocks until buffered events
  are delivered; the server calls it after each `finish_task` and on shutdown, the lab
  runner at the end of a batch.
- A `NullExperimentClient` with a fixed style is used in tests and offline runs; it records
  every call.

Exact SDK method names, the events flag and minimum versions are confirmed in SA-02 and
recorded in `docs/spike-findings.md`.

### 3.6 Ledger (`core/ledger.py`)

SQLAlchemy Core, one code path for SQLite (tests, offline) and Postgres (the real run).
**Why a ledger in addition to Flagsmith:** per-model and per-scenario breakdowns, failure
transcripts for the operator, reconciliation against Flagsmith's counts, and proof of
where traffic came from.

```mermaid
erDiagram
    SESSIONS ||--o{ TOOL_CALLS : has
    SESSIONS {
        string session_id PK
        string scenario_id
        string scenario_kind
        string source "lab | field"
        string agent_label
        string variant "null until exposed"
        bool in_experiment
        datetime started_at
        datetime exposed_at
        datetime finished_at
        string outcome
        string final_state "checker result at finalisation (added in SA-07)"
        int tool_call_count
        string client_ip_hash "field only; salted hex digest"
    }
    TOOL_CALLS {
        int id PK
        string session_id FK
        int seq "unique per session"
        string tool
        json arguments
        json result "8 KB cap; larger results become a preview"
        bool result_truncated
        string error_code
        bool styled
        datetime at
    }
    LAB_RUNS {
        string run_id PK
        int plan_order "claim order"
        string model
        string scenario_id
        int replicate "unique with model and scenario_id"
        string status "planned | running | done | failed"
        string session_id
        string failure_reason
        datetime updated_at
    }
    MODEL_REQUESTS {
        int id PK
        datetime at
        string model
        string run_id
        string status "ok | retry_later | quota_exhausted | error"
    }
    AUDIT_LOG {
        int id PK
        datetime at
        string action
        string experiment_slug
        string detail
        string approved_by
    }
```

- All timestamps are stored and returned as UTC. The schema is created on start-up with
  `create_all`; there is no migration tool.
- `finish_session` is idempotent: only the first call stores an outcome. `record_exposure`
  likewise keeps the first exposure.
- `claim_next_run` is a compare-and-set (`UPDATE ... WHERE status = 'planned'`), so two
  runners can never claim the same run, on SQLite or Postgres.
- `start_session` accepts only a 16-64 character lowercase hex digest as `client_ip_hash`,
  so a raw IP address cannot be written through the ledger's API.
- Postgres URLs use the psycopg 3 driver: `postgresql+psycopg://user:pass@host/db`.

### 3.7 Lab runner (`lab/`)

For each planned run (model x scenario x replicate), the runner opens an MCP client
connection to `SA_SERVER_URL`, calls `begin_task` itself, hands the brief and tool list to
the model, and loops on function calls until the model stops or a turn limit is reached.
It then calls `finish_task` if the model did not.

- `ModelClient` is a small protocol with one Gemini implementation. This is the only
  abstraction added for a contingency (SA-19).
- The runner never learns the variant. The server does not return it.
- The system prompt is fixed and recorded in `HYPOTHESIS.md`.
- Pacing respects per-model request limits (`SA_LAB_REQUESTS_PER_MINUTE`). A per-minute
  429 or a 5xx server error backs off and retries the same turn. A per-day 429, or reaching the
  local `SA_LAB_REQUESTS_PER_DAY` budget, parks that model until the next reset (midnight
  Pacific) while other models continue. Any other error fails the run, which is not retried;
  three consecutive failures stop the runner.
- Every model request is logged in `model_requests`, which `status` uses for the requests
  used today and the average requests per session.
- Runs are resumable from the `lab_runs` table. An interrupted run (Ctrl+C or a quota error
  mid-session) returns to `planned`; its orphan session is left for the server's idle
  expiry, which finalises it as `abandoned`. Runs left `running` by a killed runner are
  requeued at the next start once they are older than `SA_SESSION_IDLE_SECONDS`.
- `plan --replicates N` adds N more replicates per model and scenario, shuffled with a seed
  that is printed and stored in `audit_log` (action `lab_plan`).
- Lab sessions identify themselves with the `SA_LAB_KEY` header and an agent label.
  Sessions without a valid key are recorded as `field`.

### 3.8 Operator agent (`operator/`, command `sa`)

| Command | What it does | Approval |
|---|---|---|
| `sa doctor` | Checks settings, spec, CLI login, environment, warehouse connection, server SDK key, database, model key, server. | none |
| `sa propose` | Reads failing sessions from the ledger, drafts an experiment spec and rationale. | human edits and commits the file |
| `sa start <spec>` | Shows the plan, then creates the flag, variants, metrics and experiment, and starts it. | typed confirmation |
| `sa status <slug>` | Enrolment, stopping-rule progress. Refuses to name a winner early. | none |
| `sa report <slug>` | Refreshes and saves results, builds the Markdown report. | none |
| `sa ship <slug>` | Ends the experiment and sets the winning value on the flag, or records a no-ship decision. | typed confirmation |

Every mutating action is written to `audit_log`.

**Spec and start (fixed in SA-09).** A spec (`experiments/<slug>.yaml`, model in
`operator/spec.py`) names the environment, flag, hypothesis, arms (each value must parse as
`ErrorStyle`), metrics, allocation and stopping rule. Each metric has a `role`: exactly one
`primary`, any number of `guardrail` or `secondary`. A guardrail maps to `not_increase` or
`not_decrease`, the others to `increase` or `decrease`. `sa start`:

1. runs the doctor checks; any failure refuses;
2. refuses if the spec or `HYPOTHESIS.md` has uncommitted changes, if `HYPOTHESIS.md` does
   not mention the slug, or if the hard end has passed;
3. reads Flagsmith and prints the plan (create or reuse each object, then start), refusing if
   an existing object differs from the spec, another experiment is active on the flag, or the
   spec's experiment has already ended;
4. asks the human to type the slug (skipped when the experiment is already running, since
   nothing changes);
5. creates the flag (default value = control arm), each variant with weight 0 (only the
   experiment assigns it), the metrics and the experiment, starts it and checks it is
   `running`;
6. writes `experiments/.state/<slug>.json` (IDs, `started_at`, commit, approver) once, and an
   `audit_log` row with `approved_by` from `git config user.name`.

`--dry-run` stops after step 3. `sa doctor` also checks that `FLAGSMITH_ENVIRONMENT_KEY`, when
set, is a server key of the spec's environment, because a key for the wrong environment sends
sessions there silently (`docs/checkpoint-a.md`).

**Status and report (fixed in SA-12).** Both read the spec, the state file, Flagsmith's latest
results and every ledger session started since `started_at`. Modules: `results.py` (typed
results, `manual.yaml`, saved files), `stats.py` (Wilson interval, chi-square, stopping rule),
`analysis.py` (all report numbers), `narrative.py` (prose), `report.py` (Markdown and status).

- **Stopping rule:** met when every arm has `min_exposed_per_arm` exposed identities in
  **Flagsmith's** results (the primary metric's `n`), or the hard end has passed. The ledger's
  live count is shown beside it but does not decide.
- **`sa status <slug>`** never refreshes. Before the rule is met it shows no lift, interval,
  win probability or leader; `--peek` shows them under a warning and writes an `audit_log` row
  (action `peek`).
- **`sa report <slug>`** asks Flagsmith to refresh (once per 5 minutes; a refusal is not an
  error), waits for new results, and saves the raw JSON as
  `reports/<slug>/results-<UTC timestamp>.json`, reusing the newest saved file when the
  results have not changed. It writes `reports/<slug>/report.md` in nine sections. Lift,
  interval and win probability are withheld before the rule is met there too.
- **Manual values:** where the API returns no inference, `reports/<slug>/manual.yaml`
  (`metrics: {<metric name>: {lift, ci_low, ci_high, chance_to_win}}`) fills it, marked
  "from dashboard".
- **Decision:** `inconclusive` before the rule or without primary inference; `ship` when the
  win probability reaches the threshold and no guardrail's lift interval lies entirely on its
  bad side; otherwise `do not ship`.
- **Reconciliation** compares, per arm, the ledger's exposures and each metric's event sum
  with Flagsmith's, also counting the ledger only up to Flagsmith's `as_of`. A difference that
  vanishes at `as_of` is event latency; anything left is reported as unexplained.
- **Prose:** sections 1 and 9 are drafted by `SA_OPERATOR_MODEL` from a JSON of display
  strings. Every number in the draft must appear in that JSON; otherwise, or with no key,
  an API error or `--no-llm`, a fixed template is used. The report names its author.

**Propose (fixed in SA-13).** `sa propose [--from-slug <slug>] [--limit N]` (`propose.py`)
drafts the next experiment from the source experiment's ledger sessions since its
`started_at`. It never calls Flagsmith; starting a proposal is still `sa start`.

- **Evidence pack (code):** outcome counts and mean tool calls by arm and scenario kind;
  per arm and kind, how many `corrected_call`s were offered and how many the agent sent
  unchanged (same tool, same arguments, session ID ignored) as its very next call; and up to
  N non-correct sessions, round-robin over arm and kind, newest first, as compact transcripts
  (tool, arguments, error code, followed or not, outcome). The best-known arm is the one
  with the best ledger mean on the source's primary metric (control on a tie); it is not the
  source experiment's decision.
- **Model step:** `SA_OPERATOR_MODEL` gets the pack, the `ErrorStyle` settings and their
  meanings, and answers JSON: slug, variant key, the settings that are true, primary metric
  (one of the source's metrics), observed pattern, hypothesis, rationale and cited session
  IDs. The variant is a list of setting names, not a map, because the Gemini API rejects
  `additionalProperties` in response schemas.
- **Validation (code):** settings must exist in `ErrorStyle` (and `corrected_call` needs
  `suggest_alternative`); the style differs from every source arm; cited IDs, and any
  session ID in the prose, are in the pack; prose numbers appear in the pack; the slug is
  not used by any spec, proposal or state file; the built spec passes `ExperimentSpec`. A
  rejected answer is retried once with the reasons; a second rejection exits 1 and saves both
  raw answers to `experiments/proposals/.failed/` (git-ignored).
- **Output:** `experiments/proposals/<slug>.yaml` (environment `production`, the best-known
  arm as control, 50/50, the source's traffic share and stopping rule; the chosen primary
  metric becomes primary and the old primary secondary) and `<slug>.md` (a "draft generated
  by a model" header, evidence tables from code, the model's text, cited sessions from the
  ledger). An `audit_log` row with action `propose` records both paths. Without a model key
  the pack is printed and the command exits 1 without drafting.

### 3.9 Public endpoint

One container on a Render free web service (`docs/deployment.md`), serving MCP over
streamable HTTP at `/mcp`, a plain landing page at `/` (what this is, what is logged, how to
connect) and `/healthz`.

Protection: per-IP session rate limit, per-session call budget, idle timeout, a cap on
concurrent sessions, and no personal data in the ledger (IP stored only as a salted hash).

The limits live in `booking/limits.py` and apply only to field `begin_task` calls; lab sessions
are exempt. In order: the kill switch (`SA_FIELD_ENABLED=false` returns `FIELD_CLOSED`), the
cap on open sessions, lab and field together (`SERVER_BUSY`, retry after 60 s), and a sliding
one-hour window per IP hash (`RATE_LIMITED`, with the seconds until a slot frees). A refused
call returns a plain error with `retry_after_seconds` where a retry can help; it creates no
session and writes nothing to the ledger, so refusals never enter the experiment.

The client IP is the socket peer, or the first valid `X-Forwarded-For` entry when
`SA_TRUST_PROXY=true`. It is hashed at once with HMAC-SHA-256 under a salt derived from
`SA_LAB_KEY` (random per process without one), truncated to 32 hex characters. Uvicorn's
access log is off, because it prints client addresses.

`GET /healthz` returns `status` (`ok`, or `degraded` if a dependency check failed), `commit`
(`SA_GIT_COMMIT`), `active_sessions`, `field_enabled`, `checks` (`database`, `flagsmith`;
`null` when not configured) and `checked_at`. The checks are cached for 30 seconds. It
answers 200 even when degraded, because the server keeps working without Flagsmith (control
style) and a host should not restart it for that. `GET /` is the static page
`booking/landing.html`.

## 4. Flagsmith setup

| Item | Value |
|---|---|
| Project | `second-audience` |
| Environments | `development` (smoke and pilot), `production` (the reported experiment) |
| Warehouse | Flagsmith managed warehouse, enabled on both environments |
| Flag | `error_style`, multivariate; control value = informative JSON; variant key `prescriptive` |
| Metrics | `task_finished` (mean of the value, down; expected direction `decrease`, primary); `task_completed` (occurrence, up; expected direction `not_decrease`, guardrail); `task_wrong_booking` (occurrence, down; expected direction `not_increase`, guardrail). Efficiency became primary in SA-09 because completion was at its ceiling in the pilot (`HYPOTHESIS.md`) |
| Split | 100% of exposed sessions, 50/50 |

## 5. Session sequence

```mermaid
sequenceDiagram
    participant A as Agent
    participant S as MCP server
    participant E as ExperimentClient
    participant F as Flagsmith
    participant L as Ledger
    A->>S: begin_task
    S->>L: insert session
    S-->>A: session_id, brief
    A->>S: book_room (conflicts)
    S->>E: style_for(session_id)
    E->>F: resolve flag (records exposure)
    F-->>E: variant
    S->>L: variant, exposed_at, tool call
    S-->>A: styled error
    A->>S: further calls
    A->>S: finish_task
    S->>S: checker scores final state
    S->>E: track(task_completed) if correct, track(task_finished, calls)
    E->>F: event
    S->>L: outcome
```

## 6. Key decisions

| Decision | Choice | Why | Alternatives considered |
|---|---|---|---|
| Language | Python | Developer's daily stack; the organisers' walkthrough uses the Python SDK for experiments. | TypeScript: docs warn the older Node SDK cannot send events. |
| MCP framework | FastMCP | Least code for tools and HTTP transport. | Low-level MCP SDK: more control, more boilerplate. |
| Flagsmith control plane | CLI plus Admin API | Flagsmith's own agent prompt uses this path and says its MCP server cannot start experiments. | Flagsmith MCP server. |
| Success metric | Deterministic state check | Objective and cheap; no judge model. | LLM judge: costs quota and adds noise. |
| Ledger store | Postgres via `SA_DATABASE_URL`, SQLite fallback | The free host's disk is ephemeral; lab and field must share one ledger. | SQLite only: data lost on restart. |
| Variant as configuration | Flag value is a style JSON | A new variant can be rolled out without a redeploy. | Variant names mapped to code paths. |
| Agent loop | Custom loop on the Gemini SDK and MCP client | Small, transparent, no framework behaviour to explain. | Agent frameworks. |

## 7. Observability and error handling

- Structured logs (JSON lines) with `session_id` on every server log line.
- The server never raises raw exceptions to clients. Unknown errors return FastMCP's masked
  tool error (`mask_error_details=True`), with no exception detail, and are logged with a
  traceback.
- `sa doctor` is the single health check for local setup; `/healthz` for the deployment.
- `sa report` includes a reconciliation table comparing ledger counts with Flagsmith's.
