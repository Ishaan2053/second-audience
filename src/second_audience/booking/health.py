"""Dependency checks behind `/healthz`, cached so a request does not always hit the network."""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

HEALTH_TTL_SECONDS = 30.0

Probe = Callable[[], bool]
"""Returns whether a dependency answered. May raise; a raise counts as unreachable."""


class HealthMonitor:
    """Runs each probe at most once per TTL and remembers the results. Safe across threads."""

    def __init__(
        self,
        probes: Mapping[str, Probe | None],
        ttl_seconds: float = HEALTH_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind named probes; a None probe is a dependency that is not configured."""
        self._probes = dict(probes)
        self._ttl = ttl_seconds
        self._clock = clock
        self._results: dict[str, bool | None] = {}
        self._checked_at: datetime | None = None
        self._checked_mono: float | None = None
        self._lock = threading.Lock()

    def checks(self) -> tuple[dict[str, bool | None], datetime]:
        """The latest results (None = not configured) and when they were taken. Blocks to probe."""
        with self._lock:
            now = self._clock()
            if self._checked_mono is None or now - self._checked_mono >= self._ttl:
                self._results = {name: _run(name, probe) for name, probe in self._probes.items()}
                self._checked_at = datetime.now(UTC)
                self._checked_mono = now
            assert self._checked_at is not None
            return dict(self._results), self._checked_at


def _run(name: str, probe: Probe | None) -> bool | None:
    """Run one probe; any exception is reported as unreachable."""
    if probe is None:
        return None
    try:
        return probe()
    except Exception as error:
        # The type only: driver messages can carry connection details.
        logger.warning("health check %s failed: %s", name, type(error).__name__)
        return False
