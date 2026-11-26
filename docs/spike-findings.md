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

Work in progress. Questions 2 to 8 need the live throwaway experiment; the rest is filled in
as each answer is verified.

### 1. Python SDK

Verified against the installed package source (`inspect.signature` / `inspect.getsource`).

- Package: `flagsmith` **6.7.0**.
- Events are enabled with `Flagsmith(environment_key=..., enable_events=True)`. Optional
  `event_processor_config=EventProcessorConfig(events_api_url, max_buffer_items=1000,
  flush_interval_seconds=10.0)`.
- Resolve **with** exposure:
  `get_experiment_flag(feature_name: str, identifier: str, traits=None) -> Flag | DefaultFlag`.
  It raises `ValueError` if events are not enabled. It sends the exposure only when the flag
  exists, is enabled, `flag.experiment.in_experiment` is true and `flag.variant` is set;
  otherwise it logs at debug level and skips.
- Resolve **without** exposure: `get_identity_flags(identifier, traits=None, *, transient=False)
  .get_flag(feature_name)`. Then record the exposure separately with
  `track_exposure_event(feature_name, identifier=None, value=None, traits=None, metadata=None)`,
  passing `value=flag.variant` and `metadata={"experiment_id": flag.experiment.id}`, which is
  exactly what `get_experiment_flag` does internally.
- Track an event: `track_event(event: str, identifier=None, value=None, traits=None,
  metadata=None)`. Event names are case-sensitive and must match the metric's event name.
- Flush: `flush_events()` blocks until buffered events are delivered, dropped or kept for
  retry. There is no `flush()`. `close(timeout_seconds=5)` stops background work.
- `Flag` carries `variant: str | None`, `reason`, and `experiment: ExperimentMetadata | None`.
- The SDK checks for a `ser.` key prefix only when `enable_local_evaluation=True`.
