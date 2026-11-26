"""Tests for `second_audience.core.experiment`.

Unit tests mock the Flagsmith SDK. The `network` test creates a temporary multivariate flag
in the Development environment with the `flagsmith` CLI (which must be logged in) and
resolves it through the real SDK with `FLAGSMITH_ENVIRONMENT_KEY` from `.env`.
"""

import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from flagsmith import Flagsmith
from flagsmith.models import DefaultFlag, ExperimentMetadata, Flag

from second_audience.config import Settings, get_settings
from second_audience.core.experiment import (
    TASK_COMPLETED,
    TASK_FINISHED,
    TASK_WRONG_BOOKING,
    FlagsmithExperimentClient,
    NullExperimentClient,
)
from second_audience.core.styles import ARM_FLAG_VALUES, CONTROL, PRESCRIPTIVE

FLAG = "error_style"


def _flag(variant: str | None = "prescriptive", *, in_experiment: bool = True) -> Flag:
    """A resolved flag as the SDK returns it for an identity."""
    return Flag(
        enabled=True,
        value=ARM_FLAG_VALUES[variant or "control"],
        feature_id=1,
        feature_name=FLAG,
        variant=variant,
        experiment=ExperimentMetadata(id=7, name="error-style-v1", in_experiment=in_experiment),
    )


def _client(resolved: object, cache_size: int = 16) -> tuple[FlagsmithExperimentClient, MagicMock]:
    """A client over a mocked SDK whose `get_experiment_flag` returns or raises `resolved`."""
    sdk = MagicMock(spec=Flagsmith)
    if isinstance(resolved, BaseException):
        sdk.get_experiment_flag.side_effect = resolved
    else:
        sdk.get_experiment_flag.return_value = resolved
    return FlagsmithExperimentClient(sdk, FLAG, cache_size=cache_size), sdk


def test_style_for_returns_the_variant_style_and_key() -> None:
    client, sdk = _client(_flag("prescriptive"))
    assert client.style_for("s-1") == (PRESCRIPTIVE, "prescriptive")
    sdk.get_experiment_flag.assert_called_once_with(FLAG, identifier="s-1", traits=None)


def test_style_for_sends_traits_with_the_evaluation() -> None:
    client, sdk = _client(_flag("prescriptive"))
    traits = {"agent_label": "gemini-test", "source": "lab", "scenario_kind": "trap"}
    client.style_for("s-1", traits)
    sdk.get_experiment_flag.assert_called_once_with(FLAG, identifier="s-1", traits=traits)


def test_control_arm_keeps_its_variant_key() -> None:
    client, _ = _client(_flag("control"))
    assert client.style_for("s-1") == (CONTROL, "control")


def test_repeated_calls_evaluate_once() -> None:
    client, sdk = _client(_flag())
    first = client.style_for("s-1")
    second = client.style_for("s-1")
    assert first == second
    assert sdk.get_experiment_flag.call_count == 1


def test_cache_is_bounded_and_evicts_least_recently_used() -> None:
    client, sdk = _client(_flag(), cache_size=2)
    client.style_for("a")
    client.style_for("b")
    client.style_for("a")  # refreshes "a"; "b" is now the oldest
    client.style_for("c")  # evicts "b"
    assert sdk.get_experiment_flag.call_count == 3
    client.style_for("a")
    assert sdk.get_experiment_flag.call_count == 3
    client.style_for("b")
    assert sdk.get_experiment_flag.call_count == 4


def test_cache_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="cache_size"):
        FlagsmithExperimentClient(MagicMock(spec=Flagsmith), FLAG, cache_size=0)


def test_sdk_error_falls_back_to_control_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    client, sdk = _client(RuntimeError("boom"))
    assert client.style_for("s-1") == (CONTROL, None)
    assert client.style_for("s-1") == (CONTROL, None)
    assert sdk.get_experiment_flag.call_count == 1
    assert len([r for r in caplog.records if "s-1" in r.getMessage()]) == 1


def test_default_flag_falls_back_to_control() -> None:
    client, _ = _client(DefaultFlag(enabled=True, value=ARM_FLAG_VALUES["prescriptive"]))
    assert client.style_for("s-1") == (CONTROL, None)


def test_disabled_flag_falls_back_to_control() -> None:
    flag = _flag()
    flag.enabled = False
    client, _ = _client(flag)
    assert client.style_for("s-1") == (CONTROL, None)


def test_flag_outside_an_experiment_serves_its_style_without_a_variant() -> None:
    client, _ = _client(_flag("prescriptive", in_experiment=False))
    assert client.style_for("s-1") == (PRESCRIPTIVE, None)


def test_flag_with_no_experiment_metadata_is_not_enrolled() -> None:
    flag = _flag("prescriptive")
    flag.experiment = None
    client, _ = _client(flag)
    assert client.style_for("s-1") == (PRESCRIPTIVE, None)


def test_invalid_flag_value_gives_control_style() -> None:
    flag = _flag("prescriptive")
    flag.value = "not json"
    client, _ = _client(flag)
    assert client.style_for("s-1") == (CONTROL, "prescriptive")


@pytest.mark.parametrize("event", [TASK_COMPLETED, TASK_WRONG_BOOKING])
def test_track_sends_known_events(event: str) -> None:
    client, sdk = _client(_flag())
    client.track("s-1", event)
    sdk.track_event.assert_called_once_with(event, identifier="s-1", value=None)


def test_track_sends_a_numeric_value() -> None:
    client, sdk = _client(_flag())
    client.track("s-1", TASK_FINISHED, value=4)
    sdk.track_event.assert_called_once_with(TASK_FINISHED, identifier="s-1", value=4)


def test_track_rejects_unknown_events() -> None:
    client, sdk = _client(_flag())
    with pytest.raises(ValueError, match="unknown experiment event"):
        client.track("s-1", "task_done")
    sdk.track_event.assert_not_called()


def test_track_and_flush_never_raise_on_sdk_errors() -> None:
    client, sdk = _client(RuntimeError("boom"))
    sdk.track_event.side_effect = RuntimeError("boom")
    sdk.flush_events.side_effect = RuntimeError("boom")
    client.track("s-1", TASK_COMPLETED)
    client.flush()


def test_flush_forwards_to_the_sdk() -> None:
    client, sdk = _client(_flag())
    client.flush()
    sdk.flush_events.assert_called_once_with()


def test_unreachable_flagsmith_gives_control_with_the_real_sdk() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        flagsmith_environment_key="ser.not-a-real-key",
        flagsmith_api_url="http://127.0.0.1:9/api/v1/",  # nothing listens on the discard port
        flagsmith_request_timeout_seconds=1,
    )
    client = FlagsmithExperimentClient.from_settings(settings)
    assert client.style_for("s-1") == (CONTROL, None)


def test_from_settings_requires_the_environment_key() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="FLAGSMITH_ENVIRONMENT_KEY"):
        FlagsmithExperimentClient.from_settings(settings)


def test_null_client_returns_its_fixed_style_and_records_calls() -> None:
    client = NullExperimentClient(PRESCRIPTIVE, "prescriptive")
    assert client.style_for("s-1") == (PRESCRIPTIVE, "prescriptive")
    client.track("s-1", TASK_COMPLETED)
    client.flush()
    assert client.resolved == ["s-1"]
    assert client.tracked == [("s-1", TASK_COMPLETED, None)]
    assert client.flushes == 1
    with pytest.raises(ValueError, match="unknown experiment event"):
        client.track("s-1", "nope")


# --- network: real Flagsmith, Development environment -------------------------------------

PROJECT = "second-audience"
ENVIRONMENT = "Development"


def _cli(*args: str) -> str:
    """Run the `flagsmith` CLI non-interactively against the project; return stdout."""
    exe = shutil.which("flagsmith")
    if exe is None:
        pytest.skip("the flagsmith CLI is not installed")
    result = subprocess.run(
        [exe, *args, "--project", PROJECT, "--no-input"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"flagsmith {args[:2]} failed: {result.stderr.strip()}")
    return result.stdout


def _wait_for_split(flag_name: str, timeout_seconds: float = 60, settled: int = 10) -> None:
    """Wait until every probe gets the new variants; edge propagation takes a few seconds."""
    settings = get_settings()
    if settings.flagsmith_environment_key is None:
        pytest.skip("FLAGSMITH_ENVIRONMENT_KEY is not set")
    sdk = Flagsmith(
        environment_key=settings.flagsmith_environment_key.get_secret_value(),
        api_url=settings.flagsmith_api_url,
    )
    deadline = time.monotonic() + timeout_seconds
    streak = 0
    while time.monotonic() < deadline:
        flag = sdk.get_identity_flags(f"sa-test-probe-{uuid.uuid4().hex[:8]}").get_flag(flag_name)
        # A stale edge node serves the flag as STATIC with no variant key; an updated one
        # gives every identity a key ("control" or "prescriptive"). For a few seconds both
        # answer, so one good probe is not enough: wait for a run of them.
        if isinstance(flag, Flag) and flag.variant is not None:
            streak += 1
            if streak == settled:
                return
        else:
            streak = 0
            time.sleep(1)
    raise TimeoutError(f"variants of {flag_name} did not propagate in {timeout_seconds}s")


@pytest.fixture
def temporary_flag() -> Iterator[str]:
    """A 50/50 multivariate flag with the two arm values, deleted afterwards."""
    environments = json.loads(_cli("environment", "list", "--json"))
    env_key = next(e["api_key"] for e in environments if e["name"] == ENVIRONMENT)
    name = f"sa_test_{uuid.uuid4().hex[:8]}"
    _cli("feature", "create", name, "--value", ARM_FLAG_VALUES["control"], "--enabled")
    try:
        _cli(
            "feature", "variant", "add", name,
            "--key", "prescriptive",
            "--value", ARM_FLAG_VALUES["prescriptive"],
            "--weight", "50",
            "--environment", env_key,
        )  # fmt: skip
        _wait_for_split(name)
        yield name
    finally:
        _cli("feature", "delete", name, "--yes")


@pytest.mark.network
def test_identities_get_a_stable_mix_of_both_arms(temporary_flag: str) -> None:
    settings = get_settings().model_copy(update={"sa_flag_name": temporary_flag})
    identities = [f"sa-test-{uuid.uuid4().hex[:8]}-{i:02d}" for i in range(20)]

    started = time.monotonic()
    first = FlagsmithExperimentClient.from_settings(settings)
    styles = {identity: first.style_for(identity) for identity in identities}
    # A fresh client has an empty cache, so this re-asks Flagsmith for every identity.
    again = FlagsmithExperimentClient.from_settings(settings)
    repeat = {identity: again.style_for(identity) for identity in identities}
    elapsed = time.monotonic() - started

    arms = [style for style, _ in styles.values()]
    print(
        f"identities={len(identities)} control={arms.count(CONTROL)} "
        f"prescriptive={arms.count(PRESCRIPTIVE)} resolutions={2 * len(identities)} "
        f"elapsed={elapsed:.1f}s"
    )
    assert set(arms) == {CONTROL, PRESCRIPTIVE}
    assert repeat == styles
    # No experiment runs on the temporary flag, so no identity is enrolled.
    assert all(variant is None for _, variant in styles.values())
    first.flush()
    again.flush()


def test_reachable_needs_a_real_flag_from_the_api() -> None:
    client, sdk = _client(_flag())
    sdk.get_environment_flags.return_value.get_flag.return_value = _flag()
    assert client.reachable() is True
    # The SDK serves the default handler's flag when the API call fails.
    sdk.get_environment_flags.return_value.get_flag.return_value = DefaultFlag(
        enabled=True, value=ARM_FLAG_VALUES["control"]
    )
    assert client.reachable() is False
    sdk.get_environment_flags.return_value.get_flag.assert_called_with(FLAG)
    sdk.get_experiment_flag.assert_not_called()
