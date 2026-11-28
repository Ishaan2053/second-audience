"""Flagsmith's audit-log webhook: verify, trim and store each event in the ledger.

Flagsmith posts one JSON object per audit-log event and, when the webhook has a secret, signs
the raw body with HMAC-SHA-256 in `X-Flagsmith-Signature` (hex). The route rejects a missing
or wrong signature, oversized bodies and anything that is not a JSON object. The payload is
untrusted data: only a fixed set of typed fields is kept, each cut to a bounded length, and
it is stored, never acted on. The author's email and name are dropped.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from typing import Any

from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/hooks/flagsmith-audit"
SIGNATURE_HEADER = "X-Flagsmith-Signature"

MAX_BODY_BYTES = 16 * 1024
"""Flagsmith's audit payloads are well under 1 KiB; anything above this is refused."""

_TEXT_LIMITS = {
    "created_date": 40,
    "log": 300,
    "related_object_type": 40,
    "related_object_uuid": 64,
}

Store = Callable[[str], None]
"""Writes the trimmed record (JSON) to the ledger."""


def sign(secret: str, body: bytes) -> str:
    """The hex HMAC-SHA-256 of `body` under `secret`, as Flagsmith computes it."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def signature_ok(secret: str, body: bytes, received: str | None) -> bool:
    """Whether `received` is the signature of `body`, compared in constant time."""
    return received is not None and hmac.compare_digest(sign(secret, body), received.strip())


def _text(value: Any, limit: int) -> str | None:
    """A string cut to `limit` characters, or None for any other type."""
    return value[:limit] if isinstance(value, str) else None


def _integer(value: Any) -> int | None:
    """An int (not a bool), or None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ref(value: Any) -> dict[str, Any] | None:
    """The id and name of a nested project or environment object, or None."""
    if not isinstance(value, dict):
        return None
    return {"id": _integer(value.get("id")), "name": _text(value.get("name"), 100)}


def trim(payload: dict[str, Any]) -> dict[str, Any]:
    """The fields kept from an audit event, typed and bounded; unknown keys are dropped."""
    record: dict[str, Any] = {key: _text(payload.get(key), n) for key, n in _TEXT_LIMITS.items()}
    record["related_object_id"] = _integer(payload.get("related_object_id"))
    record["project"] = _ref(payload.get("project"))
    record["environment"] = _ref(payload.get("environment"))
    author = payload.get("author")
    record["author_id"] = _integer(author.get("id")) if isinstance(author, dict) else None
    return record


async def _read_capped(request: Request) -> bytes | None:
    """The request body, or None once it exceeds `MAX_BODY_BYTES` (stops reading there)."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _status(code: int, status: str) -> JSONResponse:
    if code != 200:
        logger.warning("audit webhook refused: %s", status)  # never the body: it is untrusted
    return JSONResponse({"status": status}, status_code=code)


async def handle(request: Request, secret: str, store: Store) -> JSONResponse:
    """Verify one webhook call and store its trimmed record (off the event loop)."""
    try:
        body = await _read_capped(request)
    except ClientDisconnect:
        return _status(400, "disconnected")
    if body is None:
        return _status(413, "too_large")
    if not signature_ok(secret, body, request.headers.get(SIGNATURE_HEADER)):
        return _status(401, "bad_signature")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _status(400, "malformed")
    if not isinstance(payload, dict):
        return _status(400, "malformed")
    await asyncio.to_thread(store, json.dumps(trim(payload), sort_keys=True))
    return _status(200, "stored")
