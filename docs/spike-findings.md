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
