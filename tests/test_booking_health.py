"""Unit tests for the cached `/healthz` dependency checks."""

from second_audience.booking.health import HealthMonitor


class FakeClock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class CountingProbe:
    """A probe that counts its calls and returns a set value."""

    def __init__(self, result: bool = True) -> None:
        self.calls = 0
        self.result = result

    def __call__(self) -> bool:
        self.calls += 1
        return self.result


def test_checks_are_cached_for_the_ttl() -> None:
    clock = FakeClock()
    probe = CountingProbe()
    monitor = HealthMonitor({"database": probe}, ttl_seconds=30, clock=clock)
    first, first_at = monitor.checks()
    clock.now = 29
    second, second_at = monitor.checks()
    assert first == second == {"database": True}
    assert first_at == second_at
    assert probe.calls == 1
    clock.now = 30
    probe.result = False
    assert monitor.checks()[0] == {"database": False}
    assert probe.calls == 2


def test_a_raising_probe_is_unreachable_and_a_missing_one_is_none() -> None:
    def broken() -> bool:
        raise ConnectionError("db.example:5432 refused")

    monitor = HealthMonitor({"database": broken, "flagsmith": None})
    assert monitor.checks()[0] == {"database": False, "flagsmith": None}
