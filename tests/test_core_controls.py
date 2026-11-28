"""Unit tests for the operational controls: cache expiry, fallback and malformed config."""

import json
import logging
from typing import Any

import pytest
from flagsmith.exceptions import FlagsmithAPIError
from flagsmith.models import Flags
from pydantic import SecretStr

from second_audience.config import Settings
from second_audience.core.controls import (
    CONTROLS_TTL_SECONDS,
    FIELD_ENABLED_FLAG,
    FIELD_LIMITS_FLAG,
    Controls,
    FieldLimits,
)

FALLBACK = FieldLimits(sessions_per_ip_per_hour=10, max_concurrent_sessions=50)
REMOTE = {"sessions_per_ip_per_hour": 3, "max_concurrent_sessions": 7}


class FakeClock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def flags(enabled: bool = True, limits: Any = None, limits_enabled: bool = True) -> Flags:
    """Environment flags as the SDK builds them from an API response."""
    api = [
        {
            "feature": {"name": FIELD_ENABLED_FLAG, "id": 1},
            "enabled": enabled,
            "feature_state_value": None,
        },
        {
            "feature": {"name": FIELD_LIMITS_FLAG, "id": 2},
            "enabled": limits_enabled,
            "feature_state_value": json.dumps(REMOTE) if limits is None else limits,
        },
    ]
    return Flags.from_api_flags(api, analytics_processor=None, default_flag_handler=None)


class FakeSource:
    """Returns the queued flags (or raises the queued error) and counts requests."""

    def __init__(self, result: Flags | Exception) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> Flags:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def controls(source: FakeSource | None, clock: FakeClock | None = None) -> Controls:
    """Controls with the standard fallback over `source`."""
    return Controls(
        fallback_enabled=True,
        fallback_limits=FALLBACK,
        fetch=source,
        clock=clock or FakeClock(),
    )


def test_flagsmith_values_apply_and_are_labelled() -> None:
    values = controls(FakeSource(flags(enabled=False))).get()
    assert values.field_enabled is False
    assert values.limits == FieldLimits(**REMOTE)
    assert values.payload() == {
        "field_enabled": False,
        "sessions_per_ip_per_hour": 3,
        "max_concurrent_sessions": 7,
        "source": {"field_enabled": "flagsmith", "limits": "flagsmith"},
    }


def test_values_are_cached_until_the_ttl_expires() -> None:
    clock = FakeClock()
    source = FakeSource(flags(enabled=True))
    reader = controls(source, clock)
    assert reader.get().field_enabled is True
    source.result = flags(enabled=False)
    clock.now += CONTROLS_TTL_SECONDS - 1
    assert reader.get().field_enabled is True
    assert source.calls == 1
    clock.now += 1
    assert reader.get().field_enabled is False
    assert source.calls == 2


def test_unreachable_flagsmith_falls_back_to_settings_and_is_cached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    source = FakeSource(FlagsmithAPIError("https://edge.example/api/v1/flags/ refused"))
    reader = controls(source, clock)
    values = reader.get()
    assert values.field_enabled is True
    assert values.limits == FALLBACK
    assert values.payload()["source"] == {"field_enabled": "settings", "limits": "settings"}
    assert "edge.example" not in caplog.text
    reader.get()
    assert source.calls == 1  # an outage costs one request per TTL
    source.result = flags(enabled=False)
    clock.now += CONTROLS_TTL_SECONDS
    assert reader.get().field_enabled is False


def test_missing_flags_fall_back_to_settings() -> None:
    empty = Flags.from_api_flags([], analytics_processor=None, default_flag_handler=None)
    values = controls(FakeSource(empty)).get()
    assert (values.field_enabled, values.limits) == (True, FALLBACK)
    assert values.field_enabled_source == values.limits_source == "settings"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"sessions_per_ip_per_hour": 3}),
        json.dumps({**REMOTE, "extra": 1}),
        json.dumps({**REMOTE, "max_concurrent_sessions": 0}),
        json.dumps({**REMOTE, "sessions_per_ip_per_hour": "3"}),
        json.dumps({**REMOTE, "sessions_per_ip_per_hour": True}),
        json.dumps([3, 7]),
        "",
        42,
    ],
)
def test_malformed_limits_fall_back_without_touching_the_switch(
    raw: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    values = controls(FakeSource(flags(enabled=False, limits=raw))).get()
    assert values.limits == FALLBACK
    assert values.limits_source == "settings"
    assert values.field_enabled is False
    assert values.field_enabled_source == "flagsmith"
    assert "malformed" in caplog.text


def test_disabled_limits_flag_means_no_override() -> None:
    values = controls(FakeSource(flags(limits_enabled=False))).get()
    assert values.limits == FALLBACK
    assert values.limits_source == "settings"


def test_without_a_flag_source_settings_apply() -> None:
    values = controls(None).get()
    assert (values.field_enabled, values.limits) == (True, FALLBACK)


def test_from_settings_uses_settings_values_without_a_key() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sa_env="test",
        sa_field_enabled=False,
        sa_field_sessions_per_ip_per_hour=4,
        sa_max_concurrent_sessions=9,
    )
    values = Controls.from_settings(settings).get()
    assert values.field_enabled is False
    assert values.limits == FieldLimits(sessions_per_ip_per_hour=4, max_concurrent_sessions=9)
    assert values.field_enabled_source == values.limits_source == "settings"


def test_from_settings_without_remote_makes_no_requests() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sa_env="test",
        flagsmith_environment_key=SecretStr("ser.unused"),
        flagsmith_api_url="http://127.0.0.1:9/api/v1/",
    )
    values = Controls.from_settings(settings, remote=False).get()
    assert values.field_enabled_source == values.limits_source == "settings"


def test_from_settings_with_an_unreachable_flagsmith_uses_settings() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sa_env="test",
        flagsmith_environment_key=SecretStr("ser.unreachable"),
        flagsmith_api_url="http://127.0.0.1:9/api/v1/",
        flagsmith_request_timeout_seconds=1,
        sa_max_concurrent_sessions=9,
    )
    values = Controls.from_settings(settings).get()
    assert values.limits.max_concurrent_sessions == 9
    assert values.field_enabled_source == values.limits_source == "settings"
