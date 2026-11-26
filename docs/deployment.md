# Deployment

The public booking MCP server runs as one Docker container on a Render free web service,
built from this repository's `Dockerfile` and deployed from `main`. Anyone can connect an
agent to it. The ledger is the shared Supabase Postgres, and assignment comes from the
Production Flagsmith environment.

| Item | Value |
|---|---|
| Public URL | `https://second-audience.onrender.com` (set when the service is created; see below) |
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
- Lab runs stay local: `uv run sa lab ...` drives a local server, which writes to the same
  Postgres and the same Flagsmith environment. Do not point `SA_SERVER_URL` at the public
  host for lab runs.

## 3. Environment variables on the host

Set in the Render Dashboard (service, **Environment**). Nothing else is needed; every other
setting keeps its default from `second_audience.config.Settings`.

| Variable | Value | Source |
|---|---|---|
| `SA_ENV` | `prod` | `render.yaml` |
| `SA_TRUST_PROXY` | `true` (Render's proxy sets `X-Forwarded-For`) | `render.yaml` |
| `SA_FIELD_ENABLED` | `true` | Dashboard (kill switch, section 5) |
| `FLAGSMITH_ENVIRONMENT_KEY` | Production **server-side** key (`ser.…`) | Dashboard |
| `SA_DATABASE_URL` | Supabase **session pooler** URI, `postgresql+psycopg://postgres.<ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres?sslmode=require` | Dashboard |
| `SA_LAB_KEY` | Same value as the developer's local `.env` (lab sessions and the IP-hash salt) | Dashboard |
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
   suffix if the name is taken). Update the URL in this file and the README if it differs.
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

**Disable field sessions quickly.** Set `SA_FIELD_ENABLED=false` and **Save and deploy**. New
public sessions get `FIELD_CLOSED`; open sessions finish; lab sessions still work. For an
immediate stop of everything, **Settings > Suspend Web Service** (resume later from the same
page).

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
