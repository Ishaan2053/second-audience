# Deployment

The public booking MCP server runs as one Docker container on a Render free web service,
built from this repository's `Dockerfile` and deployed from `main`. Anyone can connect an
agent to it. The ledger is the shared Supabase Postgres, and assignment comes from the
Production Flagsmith environment.

| Item | Value |
|---|---|
| Public URL | <https://second-audience.onrender.com> |
| Region | Oregon (`render.yaml`; fixed at creation, see section 1) |
| MCP endpoint | `<public URL>/mcp` (streamable HTTP, no authentication) |
| Health | `<public URL>/healthz` |
| Landing page | `<public URL>/` |
| Host configuration | `render.yaml` (Render Blueprint) |
| Image | `Dockerfile`, built by Render; CI builds it on every pull request but pushes nothing |

## 1. Host decision

Checked on 2026-11-26 against current provider pages (Render's free-instance docs,
Hugging Face's Spaces overview and pricing pages, Koyeb's pricing FAQ as quoted by 2026
comparisons). The criteria are listed in order of importance.

| Criterion | Render free web service | Hugging Face Spaces (Docker) | Koyeb free instance |
|---|---|---|---|
| No payment card | Yes. A card is optional; without one, Render suspends free services on overage instead of billing | **No.** Docker Spaces "require a paid plan to create" (PRO, $9/month) | **No.** A card with a $29 pre-authorisation since February 2026 |
| Long-lived HTTP server, streaming responses | Yes, any Docker image; requests may run up to 100 minutes | Yes (on PRO) | Yes |
| One instance, always the same | Yes, free services are a single instance | Single container | One free instance per organization |
| Sleep and cold start | Sleeps after 15 min without inbound traffic; about 1 min to wake | Sleeps when unused | Scales to zero after 1 h; wakes in seconds |
| Request timeout long enough for MCP | 100 minutes | Not documented | Not checked (excluded earlier) |
| Deploys from GitHub | Yes, auto-deploy from a branch, optionally only after CI passes | No; push to the Space's own git repo | Yes |

**Choice: Render.** It is the only candidate that needs no payment card. The cost is the
cold start, handled in section 6.

**Region: Oregon.** The Supabase ledger is in `ap-northeast-1` (Tokyo), and Render has no
Japan region; its nearest is Singapore. Render cannot move an existing service between
regions, so changing it means deleting the service and creating the Blueprint again. The
service stayed in Oregon: Oregon to Tokyo adds roughly 30 to 40 ms per database round trip
over Singapore, a small cost next to model latency, and Oregon is closer to most US and EU
agents. To move it, change `region` in `render.yaml` first, then recreate the service
(section 4).

Other Render free-tier terms that matter here: 750 free instance hours per workspace per
calendar month, an ephemeral filesystem (the ledger is in Postgres for that reason),
Render may restart a free service at any time (open sessions are lost; the idle sweep
never sees them, so they stay unfinished in the ledger), and outbound bandwidth counts
against a monthly allowance shown in the dashboard.

## 2. What runs

- One process, one Uvicorn worker: `python -m second_audience.booking.server --transport
  http --host 0.0.0.0 --port $PORT`. Render sets `PORT` (default 10000). Sessions live in
  memory, so there must never be a second instance.
- The container runs as the non-root user `app` (uid 1000).
- `/healthz` returns the deployed commit. The Dockerfile takes it from the `SA_GIT_COMMIT`
  build argument and falls back to `RENDER_GIT_COMMIT`, which Render passes to Docker
  builds.
- Render's health check calls `/healthz`. It answers 200 even when Flagsmith or the
  database check fails (`status: degraded`), so Render does not restart the server for a
  dependency outage.
- Lab runs stay local: `uv run python -m second_audience.lab.runner run` drives a local
  server, which writes to the same Postgres and the same Flagsmith environment. Do not point
  `SA_SERVER_URL` at the public host for lab runs.

## 3. Environment variables on the host

Set in the Render Dashboard (service, **Environment**). Nothing else is needed; every other
setting keeps its default from `second_audience.config.Settings`.

| Variable | Value | Source |
|---|---|---|
| `SA_ENV` | `prod` | `render.yaml` |
| `SA_TRUST_PROXY` | `true` (Render's proxy sets `X-Forwarded-For`) | `render.yaml` |
| `SA_FIELD_ENABLED` | `true` | Dashboard (fallback for the `field_enabled` flag, section 5) |
| `FLAGSMITH_ENVIRONMENT_KEY` | Production **server-side** key (`ser.…`) | Dashboard |
| `SA_DATABASE_URL` | Supabase **session pooler** URI, `postgresql+psycopg://postgres.<ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres?sslmode=require` | Dashboard |
| `SA_LAB_KEY` | Same value as the developer's local `.env` (lab sessions and the IP-hash salt) | Dashboard |
| `SA_WEBHOOK_TOKEN` | Optional. The secret of Flagsmith's audit-log webhook (section 11); unset disables the route | Dashboard |
| `PORT`, `RENDER_GIT_COMMIT` | Set by Render | Render |

Never set on the host: `GEMINI_API_KEY` or any model key, and any Flagsmith admin
credential (the `flagsmith` CLI login, an organisation API key). The server needs only the
environment key, which reads flags and writes events.

Use the session pooler, not the direct host (IPv6 only on Supabase's free plan) and not the
transaction pooler on port 6543 (no prepared statements). See `docs/spike-findings.md`.

## 4. First-time setup

1. Sign in to <https://dashboard.render.com> with GitHub. No payment method is needed.
2. **New > Blueprint**, connect the `second-audience` repository, branch `main`. Render
   reads `render.yaml` and proposes one free web service named `second-audience`.
3. Fill in the four prompted values from section 3, then **Deploy Blueprint**. The first
   build takes a few minutes.
4. Note the service URL Render assigns (`https://second-audience.onrender.com`, or with a
   suffix if the name is taken). If it differs, update it in this file, the README and the
   `SA_PUBLIC_URL` variable.
5. Check it from PowerShell:

   ```powershell
   curl.exe -s https://second-audience.onrender.com/healthz
   ```

   Expect `"status":"ok"`, the commit of `main`, and `"database":true,"flagsmith":true`.
6. In GitHub, **Settings > Secrets and variables > Actions > Variables**, add
   `SA_PUBLIC_URL` = the service URL, without a trailing slash. This turns on the keep-alive
   workflow (section 6).

## 5. Operating it

**Redeploy.** Merging to `main` deploys automatically once CI passes
(`autoDeployTrigger: checksPass`). To redeploy by hand: service, **Manual Deploy > Deploy
latest commit**. To roll back: **Events**, pick an earlier deploy, **Rollback** (free
services keep the two most recent previous deploys). After any deploy, confirm the commit
at `/healthz`.

**Change a setting.** Service, **Environment**, edit, **Save and deploy**. Settings are read
once at start, so a change applies only after the deploy finishes. Values declared with a
`value:` in `render.yaml` (`SA_ENV`, `SA_TRUST_PROXY`) are changed in the file, not the
Dashboard, or the next Blueprint sync reverts them.

**Disable field sessions quickly.** One command, no redeploy (needs `flagsmith login`):

```powershell
flagsmith flag disable field_enabled -p second-audience -e Ek4jqbHfVQuaQ4Gi6vzkhs --yes
```

Within about a minute (the server caches the controls for 60 s) new public sessions get
`FIELD_CLOSED`; open sessions finish; lab sessions still work. Turn them back on with
`flagsmith flag enable field_enabled -p second-audience -e Ek4jqbHfVQuaQ4Gi6vzkhs --yes`.
`/healthz` shows the value in force under `controls`. If Flagsmith itself is unreachable the
server uses `SA_FIELD_ENABLED`, so the slow fallback is still to set `SA_FIELD_ENABLED=false`
and **Save and deploy**. For an immediate stop of everything, **Settings > Suspend Web
Service** (resume later from the same page).

**Change the field limits.** Also without a redeploy:

```powershell
flagsmith flag update field_limits -p second-audience -e Ek4jqbHfVQuaQ4Gi6vzkhs --type string --yes --value '{"sessions_per_ip_per_hour": 10, "max_concurrent_sessions": 50}'
```

Both keys are required and must be positive integers. A malformed value, or disabling the
flag, makes the server use `SA_FIELD_SESSIONS_PER_IP_PER_HOUR` and
`SA_MAX_CONCURRENT_SESSIONS` (logged as a warning, and shown as `"limits": "settings"` at
`/healthz`). Section 10 records how the flags were created.

**Read logs.** Service, **Logs**, or with the Render CLI:

```powershell
render logs --resources <service-id> --tail
```

Client IPs are never logged (the access log is off). A start logs the Flagsmith and
database setup; a missing Flagsmith key logs a warning that every session gets the control
style.

**Check field traffic.** Field sessions are in the ledger with `source = 'field'`:

```sql
select session_id, agent_label, variant, outcome, tool_call_count, client_ip_hash, started_at
from sessions where source = 'field' order by started_at desc limit 20;
```

## 6. Cold starts and keep-alive

A sleeping service takes about a minute to answer its first request. MCP clients usually
time out sooner, so the first connection attempt after a quiet spell can fail; retrying
after a minute works. Opening the landing page first also wakes it.

`.github/workflows/keepalive.yml` requests `/healthz` every 10 minutes, which keeps the
service awake and, because `/healthz` checks the database, also stops Supabase pausing the
free project after a week without activity. Render's terms count instance hours, not
requests: an always-awake service uses about 744 of the workspace's 750 free hours in a
31-day month. **Keep this the only free web service in the Render workspace**, or the
hours run out and Render suspends every free service until the next month. GitHub may delay
scheduled runs by several minutes, so an occasional cold start can still happen. To stop
the pings, delete the `SA_PUBLIC_URL` variable.

## 7. Connecting an MCP client

Suggested instruction for the agent (also on the landing page):

```text
Use the second-audience tools. Call begin_task, complete the task it gives you, then call finish_task.
```

**Claude Code**

```powershell
claude mcp add --transport http second-audience https://second-audience.onrender.com/mcp
```

**VS Code** (`.vscode/mcp.json` in a workspace, or the user `mcp.json`)

```json
{
  "servers": {
    "second-audience": {
      "type": "http",
      "url": "https://second-audience.onrender.com/mcp"
    }
  }
}
```

Any client that supports streamable HTTP works the same way: the URL is the only setting.

## 8. Verifying a deployment

1. `/healthz` shows `ok`, the expected commit, and both checks `true`.
2. Connect an external client (section 7), give it the instruction, and let it finish.
3. The session is in the ledger with `source = 'field'` and an `outcome`, and, if it hit a
   styled error, a `variant`.
4. Field sessions from two different networks have two different `client_ip_hash` values.
   The server reads the leftmost `X-Forwarded-For` entry, so a client that sends its own
   header can dodge the per-IP limit; the global session cap bounds the load either way
   (`docs/spike-findings.md`, SA-10).

## 9. First deployment record (2026-11-26)

- Service created from the Blueprint by the developer; region Oregon; URL
  `https://second-audience.onrender.com`. `SA_PUBLIC_URL` set; the keep-alive workflow's
  first (manual) run succeeded.
- `/healthz` at 16:28 UTC, about 7 s for the first response:
  `{"status":"ok","commit":"c5e4b154a4dcf8da2187588e2767d46176e4c8a7","active_sessions":0,"field_enabled":true,"checks":{"database":true,"flagsmith":true}}`.
  The commit is `main` at the time. No `SA_GIT_COMMIT` is set on the host, so this confirms
  Render passes `RENDER_GIT_COMMIT` to the Docker build.
- External client: Claude Code 2.1.325 in print mode (`claude -p`), with only this server
  configured (`--strict-mcp-config`, `type: http`), given the suggested instruction. It ran
  scenario `s08`: `book_room` R1 hit `ROOM_CONFLICT` with the prescriptive suggestion (R2),
  then `list_rooms`, `book_room` R2, `finish_task`, outcome `correct`.
- Ledger (Supabase Postgres), session `d6af23ea4c834b7685fc782f054999be`: `source = field`,
  `agent_label = claude-code`, `variant = prescriptive`, `in_experiment = true`,
  `outcome = correct`, `tool_call_count = 3`, `client_ip_hash` set. It is the first field
  session in the ledger.
- Not yet checked: field sessions from two different networks get different
  `client_ip_hash` values (section 8, step 4).

## 10. Operational control flags (SA-20)

The kill switch and the field limits are Flagsmith flags, so changing them needs no restart
(`docs/02-architecture.md`, section 3.9). They were created on 2026-11-28 with the
`flagsmith` CLI. `feature create` creates a feature for the whole project, so one command
per flag set the same default in Production and Development:

```powershell
flagsmith feature create field_enabled -p second-audience --enabled --description "Kill switch for public (field) sessions on the booking server. Off refuses new field sessions; lab sessions are unaffected. Read by second_audience.core.controls (SA-20)."
flagsmith feature create field_limits -p second-audience --enabled --value '{"sessions_per_ip_per_hour": 10, "max_concurrent_sessions": 50}' --description "Remote config JSON for public (field) session limits on the booking server. Disabled or malformed means the server's SA_* settings apply. Read by second_audience.core.controls (SA-20)."
```

Both defaults equal the settings defaults, so creating them changed nothing for the running
service. Environment keys: Production `Ek4jqbHfVQuaQ4Gi6vzkhs`, Development
`JDfxGiKY3RS8ByAPE4CVCP` (client-side keys, as `flagsmith environment list` prints them).
Check the state with `flagsmith flag list -p second-audience -e <key>`. Write commands
(`flag enable`, `flag disable`, `flag update`) need `--yes` when run without an interactive
terminal.

**Request volume.** The server reads both flags in one environment-flags request, at most
once per 60 seconds, and only when a field `begin_task` or `/healthz` needs them. The upper
bound is 1,440 requests a day (about 44,600 in a 31-day month) if something asked every
minute without pause. The expected volume is far lower: the keep-alive pings `/healthz`
every 10 minutes (about 4,500 a month), plus at most one more read in each minute in which
field sessions begin. This comes on top of the existing `/healthz` Flagsmith probe (cached
30 s) and the experiment's identity requests, against the free plan's 50,000 requests a
month.

## 11. Flagsmith integrations (SA-22)

Plan check on 2026-11-28, organisation `Second Audience` (id 42163), plan `free`, read-only
API calls with the `flagsmith` CLI:

| Integration | Free plan | Evidence |
|---|---|---|
| Audit-log webhook | API open; delivery unverified | `api/v1/organisations/42163/webhooks/` answers 200 and allows `POST`. But the subscription metadata reports `audit_log_visibility_days: 0`, `api/v1/audit/` returns no rows, and Flagsmith's dashboard code lists the audit log (`AUDIT`) as a Scale-Up feature. Whether events are delivered on the free plan is known only once the webhook is set up |
| Slack | API open | `api/v1/environments/<key>/integrations/slack/` answers 200 (empty) |
| GitHub | API open | `api/v1/organisations/42163/integrations/github/` answers 200 (empty) |

**Audit-log webhook setup** (developer, once the SA-22 commit is deployed):

1. Generate a secret: `uv run python -c "import secrets; print(secrets.token_hex(20))"`.
2. Render Dashboard, service, **Environment**: add `SA_WEBHOOK_TOKEN` with it, **Save and
   deploy** (this restarts the service and drops in-flight field sessions).
3. Flagsmith, **Organisation settings > Webhooks > Audit log webhook**: URL
   `https://second-audience.onrender.com/hooks/flagsmith-audit`, the same secret. The webhook
   is organisation-wide, so it also reports Production changes; it never changes anything.
4. Toggle a flag in **Development** (for example `flagsmith flag disable field_limits -p
   second-audience -e JDfxGiKY3RS8ByAPE4CVCP --yes`, then `enable`), and check the ledger:

   ```sql
   select at, detail from audit_log where action = 'flagsmith_audit' order by at desc limit 5;
   ```

   No row means either the free plan does not deliver audit events or the secrets differ
   (the server answers 401). Render's logs show `audit webhook refused: bad_signature` for the latter;
   record which in this section.

**Slack and GitHub** need the developer's OAuth sign-in in the Flagsmith dashboard: Slack
under the Development environment's **Integrations**, posting to a channel in the personal
workspace; GitHub under **Organisation settings > Integrations**, then link `error_style` to
the SA-09 issue (#18) from the flag's **Links** tab. Screenshots of a Slack message from an
operator action in Development and of the flag on the GitHub issue go in `docs/img/`.
