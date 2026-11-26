"""Abuse protection for the public endpoint: kill switch, concurrency cap and per-IP rate limit.

Limits apply only when a field session begins. Lab sessions (a valid lab key) are exempt, and
a refused `begin_task` creates no session and records nothing in the ledger. The client IP is
hashed as soon as it is read; the raw address is never stored or logged.
"""

import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from second_audience.booking.errors import error_payload

RATE_LIMITED = "RATE_LIMITED"
SERVER_BUSY = "SERVER_BUSY"
FIELD_CLOSED = "FIELD_CLOSED"

WINDOW_SECONDS = 3600.0
BUSY_RETRY_SECONDS = 60
IP_HASH_LENGTH = 32
FORWARDED_FOR_HEADER = "x-forwarded-for"

_SALT_CONTEXT = b"second-audience/client-ip/v1"
_SWEEP_SECONDS = 60.0


def ip_salt(lab_key: str | None) -> bytes:
    """The IP-hash salt, derived from `SA_LAB_KEY`; random per process when no key is set."""
    if not lab_key:
        # Without a lab key (local dev) hashes are unlinkable across restarts, which is fine.
        return secrets.token_bytes(32)
    return hashlib.sha256(_SALT_CONTEXT + lab_key.encode()).digest()


def hash_ip(ip: str, salt: bytes) -> str:
    """A salted SHA-256 (HMAC) of the address, truncated to `IP_HASH_LENGTH` hex characters."""
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()[:IP_HASH_LENGTH]


def _valid_ip(value: str) -> bool:
    """Whether `value` parses as an IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip(headers: Mapping[str, str], peer: str | None, trust_proxy: bool) -> str | None:
    """The client address: the first `X-Forwarded-For` entry behind a trusted proxy, else the peer.

    `headers` must have lower-case names. A missing or malformed forwarded value falls back to
    the socket peer.
    """
    if trust_proxy:
        forwarded = headers.get(FORWARDED_FOR_HEADER, "").split(",")[0].strip()
        if _valid_ip(forwarded):
            return forwarded
    return peer


class SlidingWindowLimiter:
    """Allows at most `limit` events per key in any `window_seconds` span. Safe across threads."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Set the limit and window; `clock` is injectable for tests."""
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._last_sweep = clock()
        self._lock = threading.Lock()

    def acquire(self, key: str) -> float | None:
        """Count one event for `key` and return None, or return seconds until a slot frees."""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            self._sweep(now, cutoff)
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                return events[0] - cutoff
            events.append(now)
            return None

    def _sweep(self, now: float, cutoff: float) -> None:
        """Forget keys whose newest event has left the window, at most once a minute."""
        if now - self._last_sweep < _SWEEP_SECONDS:
            return
        self._last_sweep = now
        for key in [k for k, events in self._events.items() if not events or events[-1] <= cutoff]:
            del self._events[key]


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a field `begin_task` was refused, and when to retry."""

    code: str
    message: str
    retry_after_seconds: int | None = None

    def payload(self) -> dict[str, Any]:
        """The plain error payload returned instead of a session."""
        payload = error_payload(self.code, self.message)
        if self.retry_after_seconds is not None:
            payload["error"]["retry_after_seconds"] = self.retry_after_seconds
        return payload


class FieldGate:
    """Decides whether a new field session may begin. Callers serialise admit-and-begin."""

    def __init__(
        self,
        *,
        enabled: bool,
        max_concurrent_sessions: int,
        sessions_per_ip_per_hour: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the kill switch, the global cap and the per-IP hourly limit."""
        self._enabled = enabled
        self._max_concurrent = max_concurrent_sessions
        self._per_ip = sessions_per_ip_per_hour
        self._limiter = SlidingWindowLimiter(sessions_per_ip_per_hour, WINDOW_SECONDS, clock)

    def admit(self, ip_hash: str | None, open_sessions: int) -> Refusal | None:
        """None if the session may begin (and counts it against the IP), else the refusal.

        Without an address (stdio transport) only the kill switch and the cap apply.
        """
        if not self._enabled:
            return Refusal(
                FIELD_CLOSED, "This public endpoint is not accepting new sessions right now."
            )
        if open_sessions >= self._max_concurrent:
            return Refusal(
                SERVER_BUSY,
                f"The server is at its limit of {self._max_concurrent} open sessions. "
                f"Retry in {BUSY_RETRY_SECONDS} seconds.",
                BUSY_RETRY_SECONDS,
            )
        if ip_hash is None:
            return None
        wait = self._limiter.acquire(ip_hash)
        if wait is None:
            return None
        retry = max(1, math.ceil(wait))
        return Refusal(
            RATE_LIMITED,
            f"At most {self._per_ip} new sessions per hour are allowed from one address. "
            f"Retry in {retry} seconds.",
            retry,
        )
