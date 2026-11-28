"""The public middleware API (`SecondAudience`) with `NullExperimentClient`, and its imports."""

import ast
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select

from second_audience.config import Settings
from second_audience.core import (
    CONTROL,
    PRESCRIPTIVE,
    TASK_COMPLETED,
    TASK_FINISHED,
    FlagsmithExperimentClient,
    Ledger,
    NullExperimentClient,
    SecondAudience,
)
from second_audience.core.ledger import sessions

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "second_audience" / "core"
EXAMPLES = ROOT / "examples"


def _imported_modules(path: Path) -> set[str]:
    """Every module a Python file imports, absolute names only."""
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    "path",
    [*sorted(CORE.glob("*.py")), *sorted(EXAMPLES.rglob("*.py"))],
    ids=lambda path: path.relative_to(ROOT).as_posix(),
)
def test_core_and_examples_do_not_import_the_booking_server(path: Path) -> None:
    imported = _imported_modules(path)
    assert not {
        name
        for name in imported
        if name == "second_audience.booking" or name.startswith("second_audience.booking.")
    }
    assert "second_audience" not in imported  # `from second_audience import booking`


def test_variant_resolves_once_and_caches() -> None:
    client = NullExperimentClient(PRESCRIPTIVE, "prescriptive")
    sa = SecondAudience(client)
    assert not sa.exposed("s-1")
    assert sa.variant("s-1", {"source": "field"}) == PRESCRIPTIVE
    assert sa.variant("s-1") == PRESCRIPTIVE
    assert sa.exposed("s-1")
    assert client.resolved == ["s-1"]
    assert client.traits == {"s-1": {"source": "field"}}


def test_outcome_is_sent_only_for_enrolled_sessions() -> None:
    client = NullExperimentClient(PRESCRIPTIVE, "prescriptive")
    sa = SecondAudience(client)
    assert sa.outcome("never-exposed", TASK_COMPLETED) is False
    sa.variant("s-1")
    assert sa.outcome("s-1", TASK_COMPLETED) is True
    assert sa.outcome("s-1", TASK_FINISHED, value=4) is True
    assert client.tracked == [("s-1", TASK_COMPLETED, None), ("s-1", TASK_FINISHED, 4)]


def test_outside_a_running_experiment_nothing_is_tracked() -> None:
    client = NullExperimentClient(CONTROL, None)  # the Flagsmith fallback: no variant
    sa = SecondAudience(client)
    assert sa.variant("s-1") == CONTROL
    assert sa.outcome("s-1", TASK_COMPLETED) is False
    sa.end("s-1")
    assert client.tracked == []
    assert client.flushes == 0


def test_unknown_event_always_raises() -> None:
    sa = SecondAudience(NullExperimentClient())
    with pytest.raises(ValueError, match="unknown experiment event"):
        sa.outcome("s-1", "clicked")


def test_end_flushes_enrolled_sessions_and_forgets_them() -> None:
    client = NullExperimentClient(PRESCRIPTIVE, "prescriptive")
    sa = SecondAudience(client)
    sa.variant("s-1")
    sa.end("s-1")
    assert client.flushes == 1
    assert not sa.exposed("s-1")
    assert sa.outcome("s-1", TASK_COMPLETED) is False
    sa.end("s-1")  # ending twice is harmless
    assert client.flushes == 1


def test_ledger_records_the_first_exposure_only() -> None:
    ledger = Ledger(create_engine("sqlite://"))
    ledger.start_session("s-1", "inventory", "example", "field", "test-agent", None)
    sa = SecondAudience(NullExperimentClient(PRESCRIPTIVE, "prescriptive"), ledger)
    sa.variant("s-1")
    sa.variant("s-1")
    with ledger._engine.connect() as conn:
        row = conn.execute(select(sessions).where(sessions.c.session_id == "s-1")).one()
    assert row.variant == "prescriptive"
    assert row.in_experiment is True
    assert row.exposed_at is not None


def test_from_settings_is_offline_without_a_key() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    sa = SecondAudience.from_settings(settings, flag_name="any_flag")
    assert sa.variant("s-1") == CONTROL
    assert sa.outcome("s-1", TASK_COMPLETED) is False


def test_from_settings_uses_the_given_flag_name() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        flagsmith_environment_key=SecretStr("ser.not-a-real-key"),
    )
    client = FlagsmithExperimentClient.from_settings(settings, "stock_error_style")
    assert client._flag_name == "stock_error_style"
    assert FlagsmithExperimentClient.from_settings(settings)._flag_name == "error_style"
