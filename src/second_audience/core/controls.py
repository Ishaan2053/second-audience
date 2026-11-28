"""Operational controls for the public endpoint, read from Flagsmith with a settings fallback.

Two environment-level flags (no identity, so no exposure and no effect on the experiment):

- `field_enabled`: the kill switch. Its enabled state is the value.
- `field_limits`: remote config JSON, `{"sessions_per_ip_per_hour": int,
  "max_concurrent_sessions": int}`. A disabled flag means "no override".

Values are cached for `CONTROLS_TTL_SECONDS`, which bounds Flagsmith requests to one a minute
per process. Each value falls back to its `Settings` counterpart when Flagsmith is unreachable,
the flag is missing, or the value is malformed, and the result says which source applied.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self

from flagsmith.models import Flags
from pydantic import BaseModel, ConfigDict, Field

from second_audience.config import Settings
from second_audience.core.experiment import sdk_from_settings

logger = logging.getLogger(__name__)

FIELD_ENABLED_FLAG = "field_enabled"
FIELD_LIMITS_FLAG = "field_limits"
CONTROLS_TTL_SECONDS = 60.0

Source = Literal["flagsmith", "settings"]

FetchFlags = Callable[[], Flags]
"""Returns the environment's flags. May raise; a raise means Flagsmith is unreachable."""


class FieldLimits(BaseModel):
    """The `field_limits` remote config. Unknown keys are rejected as malformed."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    sessions_per_ip_per_hour: int = Field(gt=0)
    max_concurrent_sessions: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class ControlValues:
    """The control values in force, and where each came from."""

    field_enabled: bool
    limits: FieldLimits
    field_enabled_source: Source
    limits_source: Source

    def payload(self) -> dict[str, object]:
        """The values and their sources, as shown at `/healthz`."""
        return {
            "field_enabled": self.field_enabled,
            "sessions_per_ip_per_hour": self.limits.sessions_per_ip_per_hour,
            "max_concurrent_sessions": self.limits.max_concurrent_sessions,
            "source": {"field_enabled": self.field_enabled_source, "limits": self.limits_source},
        }


class Controls:
    """Reads the control flags at most once per TTL and falls back per value. Thread-safe."""

    def __init__(
        self,
        fallback_enabled: bool,
        fallback_limits: FieldLimits,
        fetch: FetchFlags | None = None,
        ttl_seconds: float = CONTROLS_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the settings fallback and the flag source; no `fetch` means settings only."""
        self._fallback_enabled = fallback_enabled
        self._fallback_limits = fallback_limits
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._clock = clock
        self._values: ControlValues | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, *, remote: bool = True) -> Self:
        """Controls over Flagsmith when `remote` and a key is configured, else settings only."""
        fetch: FetchFlags | None = None
        if remote and settings.flagsmith_environment_key is not None:
            # A separate client without a default flag handler: a failed request raises, so
            # it cannot be mistaken for flag values.
            fetch = sdk_from_settings(settings).get_environment_flags
        return cls(
            fallback_enabled=settings.sa_field_enabled,
            fallback_limits=FieldLimits(
                sessions_per_ip_per_hour=settings.sa_field_sessions_per_ip_per_hour,
                max_concurrent_sessions=settings.sa_max_concurrent_sessions,
            ),
            fetch=fetch,
        )

    def get(self) -> ControlValues:
        """The current values, refreshed from Flagsmith when the cache is older than the TTL."""
        with self._lock:
            now = self._clock()
            if self._values is None or now - self._fetched_at >= self._ttl:
                # A failed refresh is cached too, so an outage costs one request per TTL.
                self._values = self._read()
                self._fetched_at = now
            return self._values

    def _read(self) -> ControlValues:
        """Fetch the flags once and resolve each value, falling back where needed."""
        flags: Flags | None = None
        if self._fetch is not None:
            try:
                flags = self._fetch()
            except Exception as error:
                # The type only: SDK messages can carry the request URL.
                logger.warning(
                    "control flags unavailable (%s); using settings", type(error).__name__
                )
        enabled, enabled_source = self._field_enabled(flags)
        limits, limits_source = self._limits(flags)
        return ControlValues(enabled, limits, enabled_source, limits_source)

    def _field_enabled(self, flags: Flags | None) -> tuple[bool, Source]:
        """The kill switch from `field_enabled`, else the settings value."""
        if flags is None:
            return self._fallback_enabled, "settings"
        try:
            return flags.is_feature_enabled(FIELD_ENABLED_FLAG), "flagsmith"
        except Exception:
            logger.warning("flag %s missing; using settings", FIELD_ENABLED_FLAG)
            return self._fallback_enabled, "settings"

    def _limits(self, flags: Flags | None) -> tuple[FieldLimits, Source]:
        """The limits from an enabled `field_limits`, else the settings values."""
        if flags is None:
            return self._fallback_limits, "settings"
        try:
            flag = flags.get_flag(FIELD_LIMITS_FLAG)
        except Exception:
            logger.warning("flag %s missing; using settings", FIELD_LIMITS_FLAG)
            return self._fallback_limits, "settings"
        if not flag.enabled:
            return self._fallback_limits, "settings"
        try:
            if not isinstance(flag.value, str):
                raise ValueError("value is not a JSON string")
            return FieldLimits.model_validate(json.loads(flag.value)), "flagsmith"
        except ValueError:
            # Covers json.JSONDecodeError and pydantic's ValidationError.
            logger.warning("flag %s is malformed; using settings", FIELD_LIMITS_FLAG)
            return self._fallback_limits, "settings"
