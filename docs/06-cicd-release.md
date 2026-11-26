# CI/CD and Release Strategy

## Branching

- `main` is always releasable and protected: changes arrive by pull request only.
- Branch names: `<type>/SA-XX-<slug>` where type is `feat`, `fix`, `chore`, `docs` or `test`.
- One ticket, one branch, one pull request, squash-merged.

## Pull requests

Title: `SA-XX: <ticket title>`. Body template (`.github/pull_request_template.md`):

```
## Summary
## Why
Closes #<issue>
## Key decisions
## Testing (commands run and actual results)
## Known limitations
```

Labels: one of `feature`, `infrastructure`, `documentation`, `testing`, plus the priority (`P0` to `P3`).

## CI (GitHub Actions, `.github/workflows/ci.yml`)

Runs on every pull request and on pushes to `main`.

| Job | Command | Blocking |
|---|---|---|
| Lock check | `uv lock --check` | yes |
| Lint | `uv run ruff check .` | yes |
| Format | `uv run ruff format --check .` | yes |
| Types | `uv run mypy src` | yes |
| Tests | `uv run pytest -m "not network"` | yes |
| Build | `uv build` | yes |
| Docker build | `docker build .`, then start the image and wait for `/healthz` (added in SA-11) | yes |
| Dependency audit | `uvx pip-audit` | no (reported) |
| Secret scan | gitleaks action | yes |

Tests that need Flagsmith, Gemini or the network carry the `network` marker and are run
locally by the developer, with results stated in the pull request.

## Quality gates (lean workflow)

- Every ticket: CI green before merge.
- SA-17: `/code-review` over the whole codebase and `/ship` readiness check. Critical and
  high findings are fixed before `v1.0.0`. Findings and outcomes are written into the pull request.
- Invoking a skill is not evidence of passing. Report what it found.

## Deployment

- The public server is a Render free web service (`render.yaml`). It deploys from `main`
  using the repository's `Dockerfile`, only after CI passes. Steps are in `docs/deployment.md`.
- `.github/workflows/keepalive.yml` requests `/healthz` every 10 minutes when the repository
  variable `SA_PUBLIC_URL` is set.
- The deployed commit hash is exposed at `/healthz` so a deployment can be tied to a commit.
- Configuration lives in the host's environment settings, never in the image.

## Releases

Semantic versions, annotated tags and GitHub releases with notes.

| Version | Trigger | Meaning |
|---|---|---|
| `v0.1.0` | Checkpoint B (after SA-09) | Experiment live with lab sessions |
| `v0.2.0` | After SA-11 | Public field endpoint |
| `v0.3.0` | After SA-14 (or SA-12 if later tickets are cut) | Operator complete |
| `v1.0.0` | SA-17 | Hackathon submission |

`v0.1.0` release notes record the experiment freeze: the commit hash, scenario set,
suggestion heuristic, system prompt and model list in force when the experiment started.
