# Second Audience

A/B testing for the interfaces AI agents use: measure how your MCP server's wording changes
what agents accomplish, with agents as the experiment subjects and Flagsmith Experimentation
as the engine.

**Work in progress.** Built for Toggle Wars: The Flagsmith Experimentation Hackathon (Nov 26-29, 2026).

## Development

Requires [uv](https://docs.astral.sh/uv/). Commands work in Windows PowerShell and POSIX shells.

```powershell
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Copy `.env.example` to `.env` for local configuration.

## Licence

MIT. See [LICENSE](LICENSE).
