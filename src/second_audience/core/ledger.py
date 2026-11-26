"""Session ledger: a durable record of sessions, tool calls, lab runs and operator approvals.

Flagsmith holds exposures and conversions. The ledger holds what Flagsmith does not: which
model and scenario each session used, the tool-call transcript, the lab run plan and an
audit trail. SQLAlchemy Core keeps one code path for SQLite (tests) and Postgres (the run).
"""

import json
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Connection,
    DateTime,
    Dialect,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    make_url,
    select,
    update,
)

from second_audience.config import Settings

Source = Literal["lab", "field"]

MAX_RESULT_BYTES = 8 * 1024
"""Stored tool results above this size (UTF-8 JSON) are replaced by a truncated preview."""

_IP_HASH = re.compile(r"[0-9a-f]{16,64}")


class UtcDateTime(TypeDecorator[datetime]):
    """A timezone-aware UTC timestamp on every backend (SQLite drops the offset on read)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Reject naive datetimes and normalise aware ones to UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("ledger timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Attach UTC to values a backend returns without an offset."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


metadata = MetaData()

sessions = Table(
    "sessions",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("scenario_id", String(32), nullable=False),
    Column("scenario_kind", String(32), nullable=False),
    Column("source", String(8), nullable=False),
    Column("agent_label", String(200), nullable=False),
    Column("variant", String(64)),
    Column("in_experiment", Boolean),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("exposed_at", UtcDateTime()),
    Column("finished_at", UtcDateTime()),
    Column("outcome", String(32)),
    Column("final_state", String(32)),
    Column("tool_call_count", Integer, nullable=False, default=0),
    Column("client_ip_hash", String(64)),
)

tool_calls = Table(
    "tool_calls",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), ForeignKey("sessions.session_id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("tool", String(64), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("result", JSON),
    Column("result_truncated", Boolean, nullable=False, default=False),
    Column("error_code", String(64)),
    Column("styled", Boolean, nullable=False),
    Column("at", UtcDateTime(), nullable=False),
    UniqueConstraint("session_id", "seq"),
)

lab_runs = Table(
    "lab_runs",
    metadata,
    Column("run_id", String(36), primary_key=True),
    Column("plan_order", Integer, nullable=False),
    Column("model", String(100), nullable=False),
    Column("scenario_id", String(32), nullable=False),
    Column("replicate", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("session_id", String(64)),
    Column("failure_reason", Text),
    Column("updated_at", UtcDateTime(), nullable=False),
    UniqueConstraint("model", "scenario_id", "replicate"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", UtcDateTime(), nullable=False),
    Column("action", String(64), nullable=False),
    Column("experiment_slug", String(100)),
    Column("detail", Text, nullable=False),
    Column("approved_by", String(100)),
)


class RunStatus(StrEnum):
    """Lifecycle of a planned lab run."""

    PLANNED = "planned"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class UnknownSessionError(LookupError):
    """Raised when a ledger call names a session that was never started."""


@dataclass(frozen=True, slots=True)
class PlannedRun:
    """One cell of the lab plan: a model, a scenario and a replicate number."""

    model: str
    scenario_id: str
    replicate: int


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """A lab run that this caller now owns."""

    run_id: str
    model: str
    scenario_id: str
    replicate: int


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One stored tool call."""

    seq: int
    tool: str
    arguments: dict[str, Any]
    result: Any
    result_truncated: bool
    error_code: str | None
    styled: bool
    at: datetime


@dataclass(frozen=True, slots=True)
class FailingSession:
    """A finished session whose outcome was not `correct`, with its transcript."""

    session_id: str
    scenario_id: str
    scenario_kind: str
    source: str
    agent_label: str
    variant: str | None
    in_experiment: bool | None
    outcome: str
    final_state: str | None
    tool_call_count: int
    finished_at: datetime
    tool_calls: list[ToolCallRecord]


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """A session's assignment and result, without its transcript."""

    session_id: str
    scenario_id: str
    source: str
    agent_label: str
    variant: str | None
    in_experiment: bool | None
    outcome: str | None
    final_state: str | None


def make_engine(settings: Settings) -> Engine:
    """Create the ledger engine from `SA_DATABASE_URL`; Postgres connections are pre-pinged."""
    url = make_url(settings.sa_database_url.get_secret_value())
    if url.get_backend_name() == "postgresql":
        return create_engine(url, pool_pre_ping=True)
    return create_engine(url)


def _now() -> datetime:
    return datetime.now(UTC)


def _check_ip_hash(client_ip_hash: str | None) -> None:
    """Accept only a lowercase hex digest, so a raw IP address can never be stored."""
    if client_ip_hash is not None and not _IP_HASH.fullmatch(client_ip_hash):
        raise ValueError("client_ip_hash must be a 16-64 character lowercase hex digest")


def _bounded_result(result: Any) -> tuple[Any, bool]:
    """Return the result to store and whether it was truncated to `MAX_RESULT_BYTES`."""
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= MAX_RESULT_BYTES:
        return json.loads(encoded), False
    preview = encoded.encode("utf-8")[:MAX_RESULT_BYTES].decode("utf-8", errors="ignore")
    return {"preview": preview, "original_bytes": len(encoded.encode("utf-8"))}, True


class Ledger:
    """Writes and reads the ledger tables. Creates the schema if it does not exist."""

    def __init__(self, engine: Engine) -> None:
        """Bind to an engine and create any missing tables."""
        self._engine = engine
        metadata.create_all(engine)

    def start_session(
        self,
        session_id: str,
        scenario_id: str,
        scenario_kind: str,
        source: Source,
        agent_label: str,
        client_ip_hash: str | None,
    ) -> None:
        """Insert a new session row. `client_ip_hash` must be a salted hex digest, never an IP."""
        _check_ip_hash(client_ip_hash)
        with self._engine.begin() as conn:
            conn.execute(
                insert(sessions).values(
                    session_id=session_id,
                    scenario_id=scenario_id,
                    scenario_kind=scenario_kind,
                    source=source,
                    agent_label=agent_label,
                    started_at=_now(),
                    client_ip_hash=client_ip_hash,
                )
            )

    def record_exposure(self, session_id: str, variant: str | None, in_experiment: bool) -> None:
        """Record the session's first exposure; later calls leave the first one in place."""
        with self._engine.begin() as conn:
            result = conn.execute(
                update(sessions)
                .where(sessions.c.session_id == session_id, sessions.c.exposed_at.is_(None))
                .values(variant=variant, in_experiment=in_experiment, exposed_at=_now())
            )
            if result.rowcount == 0:
                self._require_session(conn, session_id)

    def record_tool_call(
        self,
        session_id: str,
        seq: int,
        tool: str,
        arguments: Mapping[str, Any],
        result: Any,
        error_code: str | None,
        styled: bool,
    ) -> None:
        """Append one tool call to a session's transcript, truncating an oversized result."""
        stored, truncated = _bounded_result(result)
        with self._engine.begin() as conn:
            self._require_session(conn, session_id)
            conn.execute(
                insert(tool_calls).values(
                    session_id=session_id,
                    seq=seq,
                    tool=tool,
                    arguments=dict(arguments),
                    result=stored,
                    result_truncated=truncated,
                    error_code=error_code,
                    styled=styled,
                    at=_now(),
                )
            )

    def finish_session(
        self, session_id: str, outcome: str, tool_call_count: int, *, final_state: str
    ) -> bool:
        """Store the outcome and scored final state once. False if already finished."""
        with self._engine.begin() as conn:
            result = conn.execute(
                update(sessions)
                .where(sessions.c.session_id == session_id, sessions.c.finished_at.is_(None))
                .values(
                    outcome=outcome,
                    final_state=final_state,
                    tool_call_count=tool_call_count,
                    finished_at=_now(),
                )
            )
            if result.rowcount == 0:
                self._require_session(conn, session_id)
                return False
            return True

    def plan_runs(self, rows: Iterable[PlannedRun]) -> int:
        """Append runs in the given order, skipping any already planned. Returns rows added."""
        with self._engine.begin() as conn:
            existing = {
                (r.model, r.scenario_id, r.replicate)
                for r in conn.execute(
                    select(lab_runs.c.model, lab_runs.c.scenario_id, lab_runs.c.replicate)
                )
            }
            next_order = conn.execute(select(func.max(lab_runs.c.plan_order))).scalar() or 0
            now = _now()
            new_rows: list[dict[str, Any]] = []
            for row in rows:
                key = (row.model, row.scenario_id, row.replicate)
                if key in existing:
                    continue
                existing.add(key)
                next_order += 1
                new_rows.append(
                    {
                        "run_id": str(uuid.uuid4()),
                        "plan_order": next_order,
                        "model": row.model,
                        "scenario_id": row.scenario_id,
                        "replicate": row.replicate,
                        "status": RunStatus.PLANNED,
                        "updated_at": now,
                    }
                )
            if new_rows:
                conn.execute(insert(lab_runs), new_rows)
            return len(new_rows)

    def claim_next_run(self, model: str) -> ClaimedRun | None:
        """Atomically move the model's earliest planned run to running and return it."""
        while True:
            with self._engine.begin() as conn:
                candidate = conn.execute(
                    select(lab_runs.c.run_id, lab_runs.c.scenario_id, lab_runs.c.replicate)
                    .where(lab_runs.c.model == model, lab_runs.c.status == RunStatus.PLANNED)
                    .order_by(lab_runs.c.plan_order)
                    .limit(1)
                ).first()
                if candidate is None:
                    return None
                # Compare-and-set: only one caller can move this row out of `planned`.
                claimed = conn.execute(
                    update(lab_runs)
                    .where(
                        lab_runs.c.run_id == candidate.run_id,
                        lab_runs.c.status == RunStatus.PLANNED,
                    )
                    .values(status=RunStatus.RUNNING, updated_at=_now())
                )
                if claimed.rowcount == 1:
                    return ClaimedRun(
                        candidate.run_id, model, candidate.scenario_id, candidate.replicate
                    )

    def complete_run(self, run_id: str, session_id: str) -> None:
        """Mark a run done and link the session it produced."""
        self._set_run(run_id, status=RunStatus.DONE, session_id=session_id)

    def fail_run(self, run_id: str, reason: str) -> None:
        """Mark a run failed with the reason; failed runs are not retried automatically."""
        self._set_run(run_id, status=RunStatus.FAILED, failure_reason=reason)

    def run_counts(self) -> dict[str, dict[str, int]]:
        """Count lab runs by model, then by status."""
        counts: dict[str, dict[str, int]] = defaultdict(dict)
        with self._engine.connect() as conn:
            for model, status, n in conn.execute(
                select(lab_runs.c.model, lab_runs.c.status, func.count()).group_by(
                    lab_runs.c.model, lab_runs.c.status
                )
            ):
                counts[model][status] = n
        return dict(counts)

    def audit(
        self, action: str, experiment_slug: str | None, detail: str, approved_by: str | None
    ) -> None:
        """Append an operator action to the audit log."""
        with self._engine.begin() as conn:
            conn.execute(
                insert(audit_log).values(
                    at=_now(),
                    action=action,
                    experiment_slug=experiment_slug,
                    detail=detail,
                    approved_by=approved_by,
                )
            )

    def failing_sessions(self, limit: int) -> list[FailingSession]:
        """Return up to `limit` finished, non-correct sessions, newest first, with tool calls."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(sessions)
                .where(sessions.c.outcome.is_not(None), sessions.c.outcome != "correct")
                .order_by(sessions.c.finished_at.desc(), sessions.c.session_id.desc())
                .limit(limit)
            ).all()
            calls: dict[str, list[ToolCallRecord]] = defaultdict(list)
            if rows:
                for call in conn.execute(
                    select(tool_calls)
                    .where(tool_calls.c.session_id.in_([r.session_id for r in rows]))
                    .order_by(tool_calls.c.session_id, tool_calls.c.seq)
                ):
                    calls[call.session_id].append(
                        ToolCallRecord(
                            seq=call.seq,
                            tool=call.tool,
                            arguments=call.arguments,
                            result=call.result,
                            result_truncated=call.result_truncated,
                            error_code=call.error_code,
                            styled=call.styled,
                            at=call.at,
                        )
                    )
        return [
            FailingSession(
                session_id=r.session_id,
                scenario_id=r.scenario_id,
                scenario_kind=r.scenario_kind,
                source=r.source,
                agent_label=r.agent_label,
                variant=r.variant,
                in_experiment=r.in_experiment,
                outcome=r.outcome,
                final_state=r.final_state,
                tool_call_count=r.tool_call_count,
                finished_at=r.finished_at,
                tool_calls=calls[r.session_id],
            )
            for r in rows
        ]

    def session_summaries(self, session_ids: Iterable[str]) -> list[SessionSummary]:
        """Return the summaries of the given sessions, ordered by start time."""
        ids = list(session_ids)
        if not ids:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(sessions)
                .where(sessions.c.session_id.in_(ids))
                .order_by(sessions.c.started_at, sessions.c.session_id)
            ).all()
        return [
            SessionSummary(
                session_id=r.session_id,
                scenario_id=r.scenario_id,
                source=r.source,
                agent_label=r.agent_label,
                variant=r.variant,
                in_experiment=r.in_experiment,
                outcome=r.outcome,
                final_state=r.final_state,
            )
            for r in rows
        ]

    def _set_run(self, run_id: str, **values: Any) -> None:
        """Update one run row, raising if it does not exist."""
        with self._engine.begin() as conn:
            result = conn.execute(
                update(lab_runs)
                .where(lab_runs.c.run_id == run_id)
                .values(updated_at=_now(), **values)
            )
            if result.rowcount == 0:
                raise LookupError(f"unknown lab run {run_id!r}")

    @staticmethod
    def _require_session(conn: Connection, session_id: str) -> None:
        """Raise `UnknownSessionError` unless the session row exists."""
        found = conn.execute(
            select(sessions.c.session_id).where(sessions.c.session_id == session_id)
        ).first()
        if found is None:
            raise UnknownSessionError(session_id)
