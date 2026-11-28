"""Public middleware API: variant-dependent responses and outcome tracking for any MCP server.

`SecondAudience` composes an `ExperimentClient` and, optionally, a `Ledger`, and adds no new
behaviour. It carries over the pattern the booking server proves: resolve the variant lazily,
at the first response that differs between arms (which records the exposure), and send outcome
events only for sessions that were exposed inside a running experiment.

    sa = SecondAudience.from_settings(flag_name="stock_error_style")
    style = sa.variant(session_id)            # first call: assignment and exposure
    sa.outcome(session_id, TASK_COMPLETED)    # no-op unless the session is enrolled
    sa.end(session_id)                        # flush and forget the session
"""

import logging
import threading
from dataclasses import dataclass
from typing import Self

from second_audience.config import Settings, get_settings
from second_audience.core.experiment import (
    ExperimentClient,
    FlagsmithExperimentClient,
    NullExperimentClient,
    Traits,
    check_event,
)
from second_audience.core.ledger import Ledger
from second_audience.core.styles import ErrorStyle

logger = logging.getLogger(__name__)


def experiment_client_from_settings(
    settings: Settings, flag_name: str | None = None
) -> ExperimentClient:
    """The Flagsmith client for `flag_name`, or an offline control-style client without a key."""
    if settings.flagsmith_environment_key is None:
        logger.warning(
            "FLAGSMITH_ENVIRONMENT_KEY is not set: every session gets the control style "
            "outside any experiment"
        )
        return NullExperimentClient()
    return FlagsmithExperimentClient.from_settings(settings, flag_name)


@dataclass(frozen=True, slots=True)
class _Exposure:
    """A session's resolved style and whether it is enrolled in a running experiment."""

    style: ErrorStyle
    in_experiment: bool


class SecondAudience:
    """Per-session variants with lazy exposure, and outcome events for enrolled sessions."""

    def __init__(self, experiment: ExperimentClient, ledger: Ledger | None = None) -> None:
        """Wrap an experiment client; with a ledger, exposures are also recorded there.

        A ledger requires each session to be started with `Ledger.start_session` first.
        """
        self._experiment = experiment
        self._ledger = ledger
        self._sessions: dict[str, _Exposure] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        flag_name: str | None = None,
        ledger: Ledger | None = None,
    ) -> Self:
        """Build on Flagsmith when `FLAGSMITH_ENVIRONMENT_KEY` is set, offline otherwise."""
        return cls(experiment_client_from_settings(settings or get_settings(), flag_name), ledger)

    def variant(self, session_id: str, traits: Traits | None = None) -> ErrorStyle:
        """The session's style. The first call assigns it and records the exposure."""
        with self._lock:
            known = self._sessions.get(session_id)
        if known is not None:
            return known.style
        style, variant = self._experiment.style_for(session_id, traits)
        exposure = _Exposure(style, in_experiment=variant is not None)
        with self._lock:
            # Two concurrent first calls both resolve; the first one stored wins.
            stored = self._sessions.setdefault(session_id, exposure)
        if stored is exposure and self._ledger is not None:
            self._ledger.record_exposure(session_id, variant, in_experiment=exposure.in_experiment)
        return stored.style

    def exposed(self, session_id: str) -> bool:
        """Whether the session has been assigned a style (and so exposed)."""
        with self._lock:
            return session_id in self._sessions

    def outcome(self, session_id: str, event: str, value: float | None = None) -> bool:
        """Send an experiment event for an enrolled session. Returns whether it was sent.

        Sessions never exposed, or exposed outside a running experiment, send nothing, so
        they cannot dilute the comparison. An unknown event name always raises.
        """
        check_event(event)
        with self._lock:
            known = self._sessions.get(session_id)
        if known is None or not known.in_experiment:
            return False
        self._experiment.track(session_id, event, value)
        return True

    def end(self, session_id: str) -> None:
        """Forget the session and, if it was enrolled, deliver its buffered events."""
        with self._lock:
            known = self._sessions.pop(session_id, None)
        if known is not None and known.in_experiment:
            self._experiment.flush()

    def flush(self) -> None:
        """Deliver every buffered event. Call at server shutdown."""
        self._experiment.flush()
