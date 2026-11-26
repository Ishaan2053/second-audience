"""Unit tests for the field limits: sliding window, gate, client IP and IP hashing."""

import pytest

from second_audience.booking.limits import (
    FIELD_CLOSED,
    IP_HASH_LENGTH,
    RATE_LIMITED,
    SERVER_BUSY,
    WINDOW_SECONDS,
    FieldGate,
    SlidingWindowLimiter,
    client_ip,
    hash_ip,
    ip_salt,
)


class FakeClock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_allows_up_to_the_limit_then_reports_the_wait() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60.0, clock)
    assert [limiter.acquire("a") for _ in range(3)] == [None, None, None]
    clock.now += 15
    assert limiter.acquire("a") == pytest.approx(45.0)


def test_limiter_window_slides_one_event_at_a_time() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, 60.0, clock)
    assert limiter.acquire("a") is None
    clock.now += 30
    assert limiter.acquire("a") is None
    clock.now += 30  # the first event leaves the window
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == pytest.approx(30.0)


def test_limiter_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(1, 60.0, FakeClock())
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None
    assert limiter.acquire("b") is None


def test_refused_attempts_do_not_extend_the_window() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, 60.0, clock)
    assert limiter.acquire("a") is None
    for _ in range(5):
        clock.now += 10
        assert limiter.acquire("a") is not None
    clock.now += 10
    assert limiter.acquire("a") is None


def test_limiter_forgets_expired_keys() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, 60.0, clock)
    for key in ("a", "b", "c"):
        limiter.acquire(key)
    clock.now += 120
    limiter.acquire("d")
    assert set(limiter._events) == {"d"}


def gate(**overrides: object) -> tuple[FieldGate, FakeClock]:
    clock = FakeClock()
    config: dict[str, object] = {
        "enabled": True,
        "max_concurrent_sessions": 5,
        "sessions_per_ip_per_hour": 2,
    }
    config.update(overrides)
    return FieldGate(clock=clock, **config), clock  # type: ignore[arg-type]


def test_gate_rate_limits_per_ip_hour() -> None:
    field_gate, clock = gate()
    assert field_gate.admit("ip1", 0) is None
    assert field_gate.admit("ip1", 0) is None
    refusal = field_gate.admit("ip1", 0)
    assert refusal is not None
    assert refusal.code == RATE_LIMITED
    assert refusal.retry_after_seconds == int(WINDOW_SECONDS)
    assert field_gate.admit("ip2", 0) is None
    clock.now += WINDOW_SECONDS
    assert field_gate.admit("ip1", 0) is None


def test_gate_caps_concurrent_sessions_without_using_the_ip_quota() -> None:
    field_gate, _ = gate(sessions_per_ip_per_hour=1)
    refusal = field_gate.admit("ip1", 5)
    assert refusal is not None
    assert refusal.code == SERVER_BUSY
    assert refusal.retry_after_seconds == 60
    assert field_gate.admit("ip1", 4) is None


def test_gate_kill_switch_refuses_everything() -> None:
    field_gate, _ = gate(enabled=False)
    refusal = field_gate.admit("ip1", 0)
    assert refusal is not None
    assert refusal.code == FIELD_CLOSED
    assert refusal.retry_after_seconds is None
    assert field_gate.admit(None, 0) is not None


def test_gate_without_an_address_applies_only_the_cap() -> None:
    field_gate, _ = gate(sessions_per_ip_per_hour=1)
    assert field_gate.admit(None, 0) is None
    assert field_gate.admit(None, 0) is None
    assert field_gate.admit(None, 5) is not None


def test_refusal_payload_is_a_plain_error_with_a_retry_hint() -> None:
    field_gate, _ = gate(sessions_per_ip_per_hour=1)
    field_gate.admit("ip1", 0)
    refusal = field_gate.admit("ip1", 0)
    assert refusal is not None
    payload = refusal.payload()
    assert payload == {
        "error": {
            "code": RATE_LIMITED,
            "message": refusal.message,
            "retry_after_seconds": int(WINDOW_SECONDS),
        }
    }
    assert "Retry in 3600 seconds" in refusal.message


def test_client_ip_ignores_forwarded_for_unless_the_proxy_is_trusted() -> None:
    headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.2"}
    assert client_ip(headers, "10.0.0.1", trust_proxy=False) == "10.0.0.1"
    assert client_ip(headers, "10.0.0.1", trust_proxy=True) == "203.0.113.7"


@pytest.mark.parametrize("forwarded", ["", "not-an-ip", " , 203.0.113.7"])
def test_client_ip_falls_back_to_the_peer_on_a_bad_forwarded_value(forwarded: str) -> None:
    assert client_ip({"x-forwarded-for": forwarded}, "10.0.0.1", trust_proxy=True) == "10.0.0.1"


def test_client_ip_accepts_ipv6() -> None:
    headers = {"x-forwarded-for": "2001:db8::1"}
    assert client_ip(headers, None, trust_proxy=True) == "2001:db8::1"


def test_hash_ip_is_salted_truncated_hex_and_hides_the_address() -> None:
    salt = ip_salt("lab-key")
    digest = hash_ip("203.0.113.7", salt)
    assert len(digest) == IP_HASH_LENGTH
    assert all(char in "0123456789abcdef" for char in digest)
    assert hash_ip("203.0.113.7", salt) == digest
    assert hash_ip("203.0.113.8", salt) != digest
    assert hash_ip("203.0.113.7", ip_salt("other-key")) != digest


def test_ip_salt_is_stable_for_a_lab_key_and_random_without_one() -> None:
    assert ip_salt("lab-key") == ip_salt("lab-key")
    assert ip_salt("lab-key") != b"lab-key"
    assert ip_salt(None) != ip_salt(None)
