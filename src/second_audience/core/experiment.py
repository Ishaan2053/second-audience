"""Experiment client: assigns each agent session an error style from a Flagsmith flag.

Exposure is lazy. The server asks for a style only when a session receives its first styled
error, and `get_experiment_flag` both resolves the variant and records the exposure in one
call (see `docs/spike-findings.md`, "Decisions for later tickets"). Flagsmith failures never
reach the caller: the session gets the control style and is left out of the experiment.
"""

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, Self

from flagsmith import Flagsmith
from flagsmith.models import DefaultFlag, Flag
from urllib3.util.retry import Retry

from second_audience.config import Settings
from second_audience.core.styles import ARM_FLAG_VALUES, CONTROL, ErrorStyle

logger = logging.getLogger(__name__)

TASK_COMPLETED = "task_completed"
TASK_WRONG_BOOKING = "task_wrong_booking"
TASK_FINISHED = "task_finished"
"""Sent for every scored, exposed session with the session's tool-call count as its value."""
EVENTS = frozenset({TASK_COMPLETED, TASK_WRONG_BOOKING, TASK_FINISHED})

DEFAULT_CACHE_SIZE = 4096

# The style a session gets, and its variant key; `None` means "not in the experiment".
Assignment = tuple[ErrorStyle, str | None]

# Identity traits stored on the Flagsmith identity (agent label, source, scenario kind).
Traits = Mapping[str, str]

_NOT_IN_EXPERIMENT: Assignment = (CONTROL, None)


class ExperimentClient(Protocol):
    """What the server and the lab runner need from the experiment engine."""

    def style_for(self, session_id: str, traits: Traits | None = None) -> Assignment:
        """Return the session's error style and variant key, recording the exposure once."""
        ...

    def track(self, session_id: str, event: str, value: float | None = None) -> None:
        """Send an event (with an optional numeric value) for the session. Never raises."""
        ...

    def flush(self) -> None:
        """Deliver buffered events. Call at shutdown and at the end of a lab batch."""
        ...


def check_event(event: str) -> None:
    """Reject event names that are not experiment metrics (a programming error)."""
    if event not in EVENTS:
        raise ValueError(f"unknown experiment event {event!r}; expected one of {sorted(EVENTS)}")


def _control_flag(feature_name: str) -> DefaultFlag:
    """Default flag the SDK serves when Flagsmith is unreachable: the control style."""
    return DefaultFlag(enabled=True, value=ARM_FLAG_VALUES["control"])


def sdk_from_settings(
    settings: Settings,
    *,
    enable_events: bool = False,
    default_flag_handler: Callable[[str], DefaultFlag] | None = None,
) -> Flagsmith:
    """A remote-evaluation Flagsmith SDK client with the project's timeout and retry policy."""
    if settings.flagsmith_environment_key is None:
        raise ValueError("FLAGSMITH_ENVIRONMENT_KEY is required for the Flagsmith client")
    return Flagsmith(
        environment_key=settings.flagsmith_environment_key.get_secret_value(),
        api_url=settings.flagsmith_api_url,
        request_timeout_seconds=settings.flagsmith_request_timeout_seconds,
        # The SDK default retries a failed connection three times, so an outage would
        # stall a tool call for four timeouts. One retry bounds it at two.
        retries=Retry(total=1, backoff_factor=0.1),
        enable_events=enable_events,
        default_flag_handler=default_flag_handler,
    )


def assignment_from_flag(flag: Flag | DefaultFlag) -> Assignment:
    """Turn a resolved flag into a style and the variant key, if the session is enrolled."""
    if not isinstance(flag, Flag) or not flag.enabled:
        return _NOT_IN_EXPERIMENT
    style = ErrorStyle.from_flag_value(flag.value if isinstance(flag.value, str) else None)
    enrolled = flag.experiment is not None and flag.experiment.in_experiment
    # Outside a running experiment (for example after a winner ships) the flag still serves a
    # style, but the session is not part of any comparison.
    return style, flag.variant if enrolled else None


class FlagsmithExperimentClient:
    """`ExperimentClient` backed by the Flagsmith Python SDK, with a per-session cache."""

    def __init__(self, sdk: Flagsmith, flag_name: str, cache_size: int = DEFAULT_CACHE_SIZE):
        """Wrap an events-enabled SDK client; `cache_size` bounds the remembered sessions."""
        if cache_size < 1:
            raise ValueError("cache_size must be at least 1")
        if not flag_name:
            raise ValueError("flag_name must not be empty")
        self._sdk = sdk
        self._flag_name = flag_name
        self._cache_size = cache_size
        self._cache: OrderedDict[str, Assignment] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, flag_name: str | None = None) -> Self:
        """Build a client from `Settings` for `flag_name` (default `SA_FLAG_NAME`)."""
        sdk = sdk_from_settings(settings, enable_events=True, default_flag_handler=_control_flag)
        return cls(sdk, settings.sa_flag_name if flag_name is None else flag_name)

    def reachable(self) -> bool:
        """Whether Flagsmith answers an environment flags request and serves the flag.

        The SDK swallows API errors and serves the default handler's flag, so a real `Flag`
        is the evidence of a successful request. No identity is involved, so no exposure.
        """
        flag = self._sdk.get_environment_flags().get_flag(self._flag_name)
        return isinstance(flag, Flag)

    def style_for(self, session_id: str, traits: Traits | None = None) -> Assignment:
        """Resolve the flag once per session, record the exposure, and cache the result."""
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None:
                self._cache.move_to_end(session_id)
                return cached
        # Resolved outside the lock so one slow request does not block other sessions. A
        # session's tool calls arrive one at a time, so a duplicate resolution is unlikely,
        # and Flagsmith counts exposed identities, not exposure events.
        assignment = self._resolve(session_id, traits)
        with self._lock:
            self._cache[session_id] = assignment
            self._cache.move_to_end(session_id)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return assignment

    def _resolve(self, session_id: str, traits: Traits | None) -> Assignment:
        """Ask Flagsmith for the session's flag; any failure yields the control style."""
        try:
            # Traits ride on the same request and are stored on the identity, so Flagsmith
            # segments can target them later (docs/08-flagsmith-addons.md, A1).
            flag = self._sdk.get_experiment_flag(
                self._flag_name, identifier=session_id, traits=dict(traits) if traits else None
            )
        except Exception:
            logger.warning(
                "Flagsmith call failed; session %s gets the control style outside the experiment",
                session_id,
                exc_info=True,
            )
            return _NOT_IN_EXPERIMENT
        if not isinstance(flag, Flag):
            # The default flag handler answered: Flagsmith was unreachable or the flag is gone.
            logger.warning(
                "flag %r unavailable; session %s gets the control style outside the experiment",
                self._flag_name,
                session_id,
            )
        return assignment_from_flag(flag)

    def track(self, session_id: str, event: str, value: float | None = None) -> None:
        """Send `event` for the session; delivery failures are logged, not raised."""
        check_event(event)
        try:
            self._sdk.track_event(event, identifier=session_id, value=value)
        except Exception:
            logger.warning("could not track %s for session %s", event, session_id, exc_info=True)

    def flush(self) -> None:
        """Block until buffered events are delivered, dropped or kept for retry."""
        try:
            self._sdk.flush_events()
        except Exception:
            logger.warning("could not flush Flagsmith events", exc_info=True)


@dataclass
class NullExperimentClient:
    """Offline `ExperimentClient` with a fixed assignment that records every call."""

    style: ErrorStyle = CONTROL
    variant: str | None = None
    resolved: list[str] = field(default_factory=list)
    traits: dict[str, dict[str, str]] = field(default_factory=dict)
    tracked: list[tuple[str, str, float | None]] = field(default_factory=list)
    flushes: int = 0

    def style_for(self, session_id: str, traits: Traits | None = None) -> Assignment:
        """Return the fixed style and variant, remembering the traits it was given."""
        self.resolved.append(session_id)
        if traits:
            self.traits[session_id] = dict(traits)
        return self.style, self.variant

    def track(self, session_id: str, event: str, value: float | None = None) -> None:
        """Record the event after the same validation as the real client."""
        check_event(event)
        self.tracked.append((session_id, event, value))

    def flush(self) -> None:
        """Count the flush."""
        self.flushes += 1
