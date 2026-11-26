"""Ledger behaviour on SQLite, and the same assertions on Postgres when one is configured.

The Postgres cases are marked `network` and run only when `SA_DATABASE_URL` points to a
Postgres database. They work in a throwaway schema that is dropped afterwards.
"""

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, make_url, select, text

from second_audience.config import Settings
from second_audience.core.ledger import (
    MAX_RESULT_BYTES,
    ClaimedRun,
    Ledger,
    PlannedRun,
    RequestStatus,
    UnknownSessionError,
    audit_log,
    lab_runs,
    make_engine,
    sessions,
)

IP_HASH = "0123456789abcdef"


def _postgres_engine() -> Iterator[Engine]:
    """Yield a Postgres engine bound to a fresh schema, dropping it afterwards."""
    settings = Settings()
    url = make_url(settings.sa_database_url.get_secret_value())
    if url.get_backend_name() != "postgresql":
        pytest.skip("SA_DATABASE_URL does not point to Postgres")
    schema = f"sa_test_{uuid.uuid4().hex[:12]}"
    base = make_engine(settings)
    with base.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield base.execution_options(schema_translate_map={None: schema})
    finally:
        with base.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.network)])
def engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    """An empty database on each backend."""
    if request.param == "sqlite":
        sqlite = create_engine("sqlite://")
        yield sqlite
        sqlite.dispose()
    else:
        yield from _postgres_engine()


@pytest.fixture
def ledger(engine: Engine) -> Ledger:
    """A ledger with its schema created."""
    return Ledger(engine)


def _start(ledger: Ledger, session_id: str = "s-1", ip_hash: str | None = IP_HASH) -> None:
    ledger.start_session(session_id, "s01", "aligned", "field", "claude-desktop", ip_hash)


def _session_row(engine: Engine, session_id: str = "s-1") -> dict[str, object]:
    with engine.connect() as conn:
        row = conn.execute(select(sessions).where(sessions.c.session_id == session_id)).one()
    return dict(row._mapping)


def test_start_session_stores_the_row_with_utc_timestamp(ledger: Ledger, engine: Engine) -> None:
    before = datetime.now(UTC)
    _start(ledger)
    row = _session_row(engine)
    assert row["scenario_id"] == "s01"
    assert row["scenario_kind"] == "aligned"
    assert row["source"] == "field"
    assert row["agent_label"] == "claude-desktop"
    assert row["client_ip_hash"] == IP_HASH
    assert row["variant"] is None
    assert row["exposed_at"] is None
    assert row["finished_at"] is None
    started = row["started_at"]
    assert isinstance(started, datetime)
    assert started.utcoffset() is not None
    assert started.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert started >= before.replace(microsecond=0)


def test_lab_session_may_omit_the_ip_hash(ledger: Ledger, engine: Engine) -> None:
    _start(ledger, ip_hash=None)
    assert _session_row(engine)["client_ip_hash"] is None


@pytest.mark.parametrize(
    "raw",
    [
        "203.0.113.7",
        "2001:db8::1",
        "::ffff:203.0.113.7",
        "0123456789ABCDEF",
        "short",
        "g" * 16,
        "a" * 65,
    ],
)
def test_raw_ip_or_non_digest_is_rejected(ledger: Ledger, engine: Engine, raw: str) -> None:
    with pytest.raises(ValueError, match="hex digest"):
        _start(ledger, ip_hash=raw)
    with engine.connect() as conn:
        assert conn.execute(select(sessions)).first() is None


def test_record_exposure_keeps_the_first_exposure(ledger: Ledger, engine: Engine) -> None:
    _start(ledger)
    ledger.record_exposure("s-1", "prescriptive", in_experiment=True)
    first = _session_row(engine)
    ledger.record_exposure("s-1", "control", in_experiment=False)
    second = _session_row(engine)
    assert first["variant"] == second["variant"] == "prescriptive"
    assert second["in_experiment"] is True
    assert second["exposed_at"] == first["exposed_at"]


def test_record_exposure_fallback_without_variant(ledger: Ledger, engine: Engine) -> None:
    _start(ledger)
    ledger.record_exposure("s-1", None, in_experiment=False)
    row = _session_row(engine)
    assert row["variant"] is None
    assert row["in_experiment"] is False
    assert row["exposed_at"] is not None


def test_record_tool_call_and_truncation(ledger: Ledger) -> None:
    _start(ledger)
    ledger.record_tool_call(
        "s-1", 1, "book_room", {"room_id": "r1"}, {"error": "ROOM_CONFLICT"}, "ROOM_CONFLICT", True
    )
    big = {"rooms": ["x" * 100 for _ in range(200)]}
    ledger.record_tool_call("s-1", 2, "list_rooms", {}, big, None, False)
    ledger.finish_session("s-1", "incorrect", 2, final_state="incorrect")

    [failing] = ledger.failing_sessions(10)
    first, second = failing.tool_calls
    assert (first.seq, first.tool, first.arguments) == (1, "book_room", {"room_id": "r1"})
    assert first.result == {"error": "ROOM_CONFLICT"}
    assert first.error_code == "ROOM_CONFLICT"
    assert first.styled is True
    assert first.result_truncated is False
    assert second.result_truncated is True
    assert second.result["original_bytes"] > MAX_RESULT_BYTES
    assert len(second.result["preview"].encode("utf-8")) <= MAX_RESULT_BYTES


def test_record_tool_call_rejects_duplicate_seq(ledger: Ledger) -> None:
    _start(ledger)
    ledger.record_tool_call("s-1", 1, "list_rooms", {}, [], None, False)
    with pytest.raises(Exception, match=r"(?i)unique"):
        ledger.record_tool_call("s-1", 1, "list_rooms", {}, [], None, False)


def test_finish_session_is_idempotent(ledger: Ledger, engine: Engine) -> None:
    _start(ledger)
    assert ledger.finish_session("s-1", "budget_exhausted", 3, final_state="correct") is True
    first = _session_row(engine)
    assert ledger.finish_session("s-1", "abandoned", 9, final_state="no_booking") is False
    second = _session_row(engine)
    assert second["outcome"] == "budget_exhausted"
    assert second["final_state"] == "correct"
    assert second["tool_call_count"] == 3
    assert second["finished_at"] == first["finished_at"]


def test_unknown_session_is_rejected(ledger: Ledger) -> None:
    with pytest.raises(UnknownSessionError):
        ledger.record_exposure("missing", "control", in_experiment=True)
    with pytest.raises(UnknownSessionError):
        ledger.record_tool_call("missing", 1, "list_rooms", {}, [], None, False)
    with pytest.raises(UnknownSessionError):
        ledger.finish_session("missing", "correct", 0, final_state="correct")


def test_plan_runs_skips_duplicates_and_keeps_order(ledger: Ledger) -> None:
    plan = [PlannedRun("m1", "s02", 1), PlannedRun("m1", "s01", 1), PlannedRun("m2", "s01", 1)]
    assert ledger.plan_runs(plan) == 3
    assert ledger.plan_runs([*plan, PlannedRun("m1", "s01", 2)]) == 1
    assert ledger.run_counts() == {"m1": {"planned": 3}, "m2": {"planned": 1}}

    claimed = [ledger.claim_next_run("m1") for _ in range(4)]
    assert [(c.scenario_id, c.replicate) for c in claimed if c] == [
        ("s02", 1),
        ("s01", 1),
        ("s01", 2),
    ]
    assert claimed[3] is None


def test_requeue_returns_runs_to_planned(ledger: Ledger) -> None:
    ledger.plan_runs([PlannedRun("m1", "s01", 1), PlannedRun("m1", "s02", 1)])
    a = ledger.claim_next_run("m1")
    b = ledger.claim_next_run("m1")
    assert a is not None
    assert b is not None
    ledger.requeue_run(a.run_id)
    assert ledger.run_counts() == {"m1": {"planned": 1, "running": 1}}
    assert ledger.requeue_stale_runs(datetime.now(UTC) - timedelta(minutes=5)) == 0
    assert ledger.requeue_stale_runs(datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert ledger.run_counts() == {"m1": {"planned": 2}}


def test_max_replicates_per_model(ledger: Ledger) -> None:
    assert ledger.max_replicates() == {}
    ledger.plan_runs([PlannedRun("m1", "s01", 1), PlannedRun("m1", "s01", 3)])
    ledger.plan_runs([PlannedRun("m2", "s02", 2)])
    assert ledger.max_replicates() == {"m1": 3, "m2": 2}


def test_model_requests_exclude_quota_refusals(ledger: Ledger) -> None:
    ledger.plan_runs([PlannedRun("m1", "s01", 1), PlannedRun("m1", "s02", 1)])
    a = ledger.claim_next_run("m1")
    b = ledger.claim_next_run("m1")
    assert a is not None
    assert b is not None
    for status in (RequestStatus.OK, RequestStatus.RETRY_LATER, RequestStatus.OK):
        ledger.record_model_request("m1", a.run_id, status)
    ledger.record_model_request("m1", b.run_id, RequestStatus.QUOTA_EXHAUSTED)
    ledger.record_model_request("m2", None, RequestStatus.ERROR)
    ledger.complete_run(a.run_id, "sess-a")
    since = datetime.now(UTC) - timedelta(minutes=1)
    assert ledger.model_requests_since(since) == {"m1": 2, "m2": 1}
    assert ledger.model_requests_since(datetime.now(UTC) + timedelta(minutes=1)) == {}
    assert ledger.requests_per_done_run() == {"m1": 2.0}


def test_complete_fail_and_counts(ledger: Ledger, engine: Engine) -> None:
    ledger.plan_runs([PlannedRun("m1", "s01", 1), PlannedRun("m1", "s02", 1)])
    a = ledger.claim_next_run("m1")
    b = ledger.claim_next_run("m1")
    assert a is not None
    assert b is not None
    ledger.complete_run(a.run_id, "sess-a")
    ledger.fail_run(b.run_id, "model returned 500")
    assert ledger.run_counts() == {"m1": {"done": 1, "failed": 1}}
    with engine.connect() as conn:
        rows = {r.run_id: r for r in conn.execute(select(lab_runs))}
    assert rows[a.run_id].session_id == "sess-a"
    assert rows[b.run_id].failure_reason == "model returned 500"
    with pytest.raises(LookupError):
        ledger.complete_run("no-such-run", "sess")


def test_claim_next_run_never_returns_a_run_twice(ledger: Ledger) -> None:
    ledger.plan_runs(PlannedRun("m1", f"s{i:02d}", 1) for i in range(20))
    seen: list[ClaimedRun] = []
    while (run := ledger.claim_next_run("m1")) is not None:
        seen.append(run)
    assert len(seen) == 20
    assert len({r.run_id for r in seen}) == 20
    assert ledger.claim_next_run("m2") is None


def _claim_concurrently(ledger: Ledger, workers: int) -> list[str]:
    claimed: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        while (run := ledger.claim_next_run("m1")) is not None:
            with lock:
                claimed.append(run.run_id)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return claimed


def test_concurrent_claims_on_a_file_database(tmp_path: Path) -> None:
    ledger = Ledger(create_engine(f"sqlite:///{tmp_path / 'ledger.db'}"))
    ledger.plan_runs(PlannedRun("m1", f"s{i:02d}", 1) for i in range(40))
    claimed = _claim_concurrently(ledger, workers=6)
    assert len(claimed) == 40
    assert len(set(claimed)) == 40


@pytest.mark.network
def test_concurrent_claims_on_postgres() -> None:
    for engine in _postgres_engine():
        ledger = Ledger(engine)
        ledger.plan_runs(PlannedRun("m1", f"s{i:02d}", 1) for i in range(40))
        claimed = _claim_concurrently(ledger, workers=6)
        assert len(claimed) == 40
        assert len(set(claimed)) == 40


def test_audit_appends_rows(ledger: Ledger, engine: Engine) -> None:
    ledger.audit("start", "error-style-v1", "started experiment 12", "ishaan")
    ledger.audit("peek", None, "status --peek", None)
    with engine.connect() as conn:
        rows = conn.execute(select(audit_log).order_by(audit_log.c.id)).all()
    assert [(r.action, r.experiment_slug, r.approved_by) for r in rows] == [
        ("start", "error-style-v1", "ishaan"),
        ("peek", None, None),
    ]
    assert all(r.at.utcoffset().total_seconds() == 0 for r in rows)


def test_failing_sessions_filters_orders_and_limits(ledger: Ledger) -> None:
    outcomes = {"a": "correct", "b": "incorrect", "c": "no_booking", "d": None, "e": "abandoned"}
    for session_id, outcome in outcomes.items():
        _start(ledger, session_id)
        ledger.record_tool_call(session_id, 1, "list_rooms", {}, [], None, False)
        if outcome is not None:
            ledger.finish_session(session_id, outcome, 1, final_state=outcome)

    failing = ledger.failing_sessions(10)
    assert [f.session_id for f in failing] == ["e", "c", "b"]
    assert [f.final_state for f in failing] == ["abandoned", "no_booking", "incorrect"]
    assert all(len(f.tool_calls) == 1 for f in failing)
    assert [f.session_id for f in ledger.failing_sessions(2)] == ["e", "c"]
    assert ledger.failing_sessions(0) == []


def test_session_summaries_return_assignment_and_result(ledger: Ledger) -> None:
    _start(ledger, "a")
    _start(ledger, "b")
    ledger.record_exposure("a", "prescriptive", in_experiment=True)
    ledger.finish_session("a", "budget_exhausted", 12, final_state="incorrect")

    summaries = {s.session_id: s for s in ledger.session_summaries(["a", "b", "missing"])}
    assert set(summaries) == {"a", "b"}
    a, b = summaries["a"], summaries["b"]
    assert (a.variant, a.in_experiment, a.outcome, a.final_state) == (
        "prescriptive",
        True,
        "budget_exhausted",
        "incorrect",
    )
    assert (b.variant, b.in_experiment, b.outcome, b.final_state) == (None, None, None, None)
    assert ledger.session_summaries([]) == []


def test_schema_creation_is_repeatable(engine: Engine) -> None:
    first = Ledger(engine)
    _start(first)
    Ledger(engine)
    assert _session_row(engine)["session_id"] == "s-1"


def test_make_engine_uses_the_configured_url(tmp_path: Path) -> None:
    db = tmp_path / "sa.db"
    engine = make_engine(Settings(sa_database_url=f"sqlite:///{db}"))  # type: ignore[arg-type]
    Ledger(engine)
    assert db.exists()
    assert engine.pool._pre_ping is False  # type: ignore[attr-defined]


def test_make_engine_pre_pings_postgres() -> None:
    url = "postgresql+psycopg://user:pw@localhost:5432/db"
    engine = make_engine(Settings(sa_database_url=url))  # type: ignore[arg-type]
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
