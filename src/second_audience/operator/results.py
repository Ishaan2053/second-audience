"""Flagsmith experiment results: typed parsing, the manual overlay, and saved raw files.

Field names follow `docs/spike-findings.md` (SA-02, question 5). Every raw results payload a
report uses is saved under `reports/<slug>/`, so each number in a report can be traced to a
file. Inference that the API does not return can be copied from the dashboard into
`reports/<slug>/manual.yaml`; the report marks those values "from dashboard".
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError

MANUAL_FILE = "manual.yaml"
RESULTS_GLOB = "results-*.json"
REFRESH_REFUSED = "requested recently"
"""Flagsmith's refusal inside the 5-minute refresh window ("A refresh was requested recently")."""
REFRESH_POLLS = 15
REFRESH_POLL_SECONDS = 2.0


class _Loose(BaseModel):
    """Flagsmith adds fields over time; only the documented ones are read."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class VariantStats(_Loose):
    """One arm of one metric: exposed identities, and the sum and sum of squares of values."""

    n: int
    sum: float
    sum_squares: float

    @property
    def mean(self) -> float | None:
        """Sum over exposed identities: a conversion rate for `occurrence`, a mean for `mean`."""
        return self.sum / self.n if self.n else None


class Inference(_Loose):
    """Flagsmith's Bayesian read of one variant against control on one metric."""

    lift: float
    ci_low: float
    ci_high: float
    chance_to_win: float


class MetricResults(_Loose):
    """One metric's results; `inference` holds `None` until Flagsmith's thresholds are met."""

    metric_id: int
    variants: dict[str, VariantStats]
    inference: dict[str, Inference | None] = {}


class ExperimentResults(_Loose):
    """The parsed `results` object; `metrics` is empty before the first refresh."""

    as_of: datetime | None = None
    is_final: bool = False
    srm_p_value: float | None = None
    metrics: list[MetricResults] = []

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "ExperimentResults":
        """Parse `GET .../experiments/<id>/results/`; `results` is null before any refresh."""
        results = raw.get("results")
        if not results:
            return cls()
        payload = results.get("payload") or {}
        return cls.model_validate(
            {
                "as_of": results.get("as_of"),
                "is_final": results.get("is_final", False),
                "srm_p_value": payload.get("srm_p_value"),
                "metrics": payload.get("metrics") or [],
            }
        )

    def metric(self, metric_id: int) -> MetricResults | None:
        """The results for one metric ID, if Flagsmith has any."""
        return next((m for m in self.metrics if m.metric_id == metric_id), None)


def load_manual(folder: Path) -> dict[str, Inference]:
    """Dashboard values from `manual.yaml`, keyed by metric name; empty if the file is absent.

    Format: `metrics: {<metric name>: {lift, ci_low, ci_high, chance_to_win}}`, for the one
    variant of a two-arm experiment.
    """
    path = folder / MANUAL_FILE
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    metrics = raw.get("metrics") if isinstance(raw, dict) else None
    if not isinstance(metrics, dict):
        raise ValueError(f"{path} must contain a `metrics:` mapping of metric name to values")
    return {str(name): Inference.model_validate(values) for name, values in metrics.items()}


@dataclass(frozen=True)
class SavedResults:
    """Results a report reads, the file they were saved to, and how fresh they are."""

    results: ExperimentResults
    path: Path
    refreshed: bool
    note: str


def latest_saved(folder: Path) -> Path | None:
    """The newest saved results file (names sort by timestamp)."""
    files = sorted(folder.glob(RESULTS_GLOB))
    return files[-1] if files else None


def _as_of(raw: dict[str, Any]) -> str | None:
    """The raw `as_of` string, or `None` before the first refresh."""
    results = raw.get("results") or {}
    value = results.get("as_of")
    return str(value) if value is not None else None


def refresh_and_save(
    admin: FlagsmithAdmin,
    env: str,
    experiment_id: int,
    folder: Path,
    now: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> SavedResults:
    """Ask Flagsmith to recompute results, wait for them, and save the raw JSON.

    Inside the 5-minute refresh window Flagsmith refuses; the latest computed results are
    used instead. When they are the ones already saved, that file is reused, not copied.
    """
    before = _as_of(admin.get_results(env, experiment_id))
    refreshed = True
    try:
        admin.refresh_results(env, experiment_id)
        note = "refreshed for this report"
    except FlagsmithAdminError as error:
        if REFRESH_REFUSED not in error.stderr:
            raise
        refreshed = False
        note = "refresh refused by Flagsmith (one per 5 minutes); latest computed results used"
    raw = admin.get_results(env, experiment_id)
    if refreshed:
        for _ in range(REFRESH_POLLS):
            if _as_of(raw) != before:
                break
            sleep(REFRESH_POLL_SECONDS)
            raw = admin.get_results(env, experiment_id)
        else:
            note = "refresh accepted but new results were not ready; latest computed results used"
            refreshed = False

    folder.mkdir(parents=True, exist_ok=True)
    previous = latest_saved(folder)
    if previous is not None and _as_of(json.loads(previous.read_text(encoding="utf-8"))) == _as_of(
        raw
    ):
        path = previous
    else:
        path = folder / f"results-{now:%Y%m%dT%H%M%SZ}.json"
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return SavedResults(ExperimentResults.from_api(raw), path, refreshed, note)
