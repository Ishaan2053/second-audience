"""`FlagsmithAdmin` command construction, with `subprocess.run` patched."""

import json
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from second_audience.operator import flagsmith_admin
from second_audience.operator.flagsmith_admin import FlagsmithAdmin, FlagsmithAdminError

ENV = "envkey123"
SUFFIX = ["--project", "second-audience", "--no-input"]


class Recorder:
    """Stands in for `subprocess.run`: records each call and answers from a responder."""

    def __init__(self, respond: Callable[[list[str]], tuple[int, Any, str]]) -> None:
        self.respond = respond
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["capture_output"] is True
        assert "shell" not in kwargs
        args = argv[1:]
        self.calls.append((args, kwargs.get("input")))
        code, payload, stderr = self.respond(args)
        stdout = "" if payload is None else json.dumps(payload)
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Recorder]:
    """Install a recorder with the given responder."""

    def install(respond: Callable[[list[str]], tuple[int, Any, str]]) -> Recorder:
        recorder = Recorder(respond)
        monkeypatch.setattr(flagsmith_admin.subprocess, "run", recorder)
        return recorder

    return install


def _admin() -> FlagsmithAdmin:
    return FlagsmithAdmin("second-audience", executable="flagsmith")


def test_cli_commands_ask_for_json_and_never_prompt(run: Callable[..., Recorder]) -> None:
    recorder = run(lambda args: (0, {"email": "dev@example.com"}, ""))
    assert _admin().whoami() == "dev@example.com"
    assert recorder.calls == [(["auth", "status", *SUFFIX, "--json"], None)]


def test_api_calls_send_the_body_on_stdin(run: Callable[..., Recorder]) -> None:
    def respond(args: list[str]) -> tuple[int, Any, str]:
        if "-X" in args:
            return 0, {"id": 9, "name": "Wrong booking"}, ""
        return 0, {"count": 0, "results": []}, ""

    recorder = run(respond)
    metric = _admin().create_metric(
        ENV, "Wrong booking", "task_wrong_booking", "occurrence", "down"
    )
    assert metric["id"] == 9
    path = f"api/v1/environments/{ENV}/experiment-metrics/"
    assert recorder.calls[0] == (["api", path, *SUFFIX], None)
    args, body = recorder.calls[1]
    assert args == ["api", path, "-X", "POST", *SUFFIX, "--input", "-"]
    assert body is not None
    assert json.loads(body) == {
        "name": "Wrong booking",
        "aggregation": "occurrence",
        "direction": "down",
        "definition": {"version": 1, "event": "task_wrong_booking"},
    }


def test_create_metric_reuses_one_with_the_same_name(run: Callable[..., Recorder]) -> None:
    recorder = run(lambda args: (0, {"results": [{"id": 4, "name": "Wrong booking"}]}, ""))
    metric = _admin().create_metric(
        ENV, "Wrong booking", "task_wrong_booking", "occurrence", "down"
    )
    assert metric["id"] == 4
    assert len(recorder.calls) == 1


def test_feature_and_variant_creation(run: Callable[..., Recorder]) -> None:
    features: list[dict[str, Any]] = []

    def respond(args: list[str]) -> tuple[int, Any, str]:
        if args[:2] == ["feature", "list"]:
            return 0, features, ""
        if args[:2] == ["feature", "create"]:
            features.append({"id": 1, "name": args[2], "default_value": args[4], "variants": []})
            return 0, features[-1], ""
        if args[:3] == ["feature", "variant", "list"]:
            return 0, features[0]["variants"], ""
        if args[:3] == ["feature", "variant", "add"]:
            features[0]["variants"].append({"id": 7, "key": args[5], "value": args[7]})
            return 0, features[0]["variants"][-1], ""
        raise AssertionError(args)

    recorder = run(respond)
    admin = _admin()
    feature = admin.create_feature("error_style", '{"a": false}')
    variant = admin.add_variant("error_style", "prescriptive", '{"a": true}', ENV)
    assert (feature["id"], variant["id"]) == (1, 7)
    commands = [args for args, _ in recorder.calls]
    assert (commands[1][:6]) == [
        "feature",
        "create",
        "error_style",
        "--value",
        '{"a": false}',
        "--enabled",
    ]
    add = next(args for args in commands if args[:3] == ["feature", "variant", "add"])
    assert add == [
        "feature", "variant", "add", "error_style",
        "--key", "prescriptive", "--value", '{"a": true}', "--weight", "0",
        *SUFFIX, "--environment", ENV, "--json",
    ]  # fmt: skip

    # A second time, both are found and nothing is created.
    before = len(recorder.calls)
    admin.create_feature("error_style", '{"a": false}')
    admin.add_variant("error_style", "prescriptive", '{"a": true}', ENV)
    assert [args[:2] for args, _ in recorder.calls[before:]] == [
        ["feature", "list"],
        ["feature", "variant"],
    ]


def test_experiment_lifecycle_paths(run: Callable[..., Recorder]) -> None:
    recorder = run(lambda args: (0, {"id": 5, "status": "running"}, ""))
    admin = _admin()
    admin.start_experiment(ENV, 5)
    admin.refresh_results(ENV, 5)
    admin.get_results(ENV, 5)
    base = f"api/v1/environments/{ENV}/experiments/5"
    assert [args[:4] for args, _ in recorder.calls] == [
        ["api", f"{base}/start/", "-X", "POST"],
        ["api", f"{base}/results/refresh/", "-X", "POST"],
        ["api", f"{base}/results/", *SUFFIX[:2]],
    ]


def test_complete_sends_the_rollout_decision(run: Callable[..., Recorder]) -> None:
    recorder = run(lambda args: (0, {"id": 5, "status": "completed"}, ""))
    admin = _admin()
    admin.complete_experiment(ENV, 5, "variant", 37)
    admin.complete_experiment(ENV, 5, "control", None)
    path = f"api/v1/environments/{ENV}/experiments/5/complete/"
    assert [(args[:4], json.loads(body or "")) for args, body in recorder.calls] == [
        (["api", path, "-X", "POST"], {"rollout_decision": "variant", "rollout_variant": 37}),
        (["api", path, "-X", "POST"], {"rollout_decision": "control"}),
    ]


def test_feature_state_is_the_environment_default(run: Callable[..., Recorder]) -> None:
    default = {"id": 1, "identity": None, "feature_segment": None, "enabled": True}
    override = {"id": 2, "identity": None, "feature_segment": 9}
    recorder = run(lambda args: (0, {"results": [default, override]}, ""))
    assert _admin().feature_state(ENV, 10) == default
    assert recorder.calls[0][0][1] == f"api/v1/environments/{ENV}/featurestates/?feature=10"
    run(lambda args: (0, {"results": [override]}, ""))
    with pytest.raises(FlagsmithAdminError, match="expected one environment default"):
        _admin().feature_state(ENV, 10)


def test_create_experiment_reuses_a_listed_one(run: Callable[..., Recorder]) -> None:
    listed = {"id": 5, "name": "error-style-v1", "feature": {"id": 10, "name": "error_style"}}
    recorder = run(lambda args: (0, {"results": [listed]}, ""))
    payload = {"name": "error-style-v1", "feature": 10}
    assert _admin().create_experiment(ENV, payload) == listed
    assert len(recorder.calls) == 1


def test_warehouse_connected_checks_existence_not_status(run: Callable[..., Recorder]) -> None:
    run(lambda args: (0, [{"id": 1, "status": "pending_connection"}], ""))
    assert _admin().warehouse_connected(ENV) is True
    run(lambda args: (0, [], ""))
    assert _admin().warehouse_connected(ENV) is False


def test_environment_key_matches_names_case_insensitively(run: Callable[..., Recorder]) -> None:
    run(lambda args: (0, [{"name": "Production", "api_key": "prodkey"}], ""))
    assert _admin().environment_key("production") == "prodkey"
    with pytest.raises(FlagsmithAdminError, match="no environment named 'staging'"):
        _admin().environment_key("staging")


def test_non_zero_exit_raises_with_stderr(run: Callable[..., Recorder]) -> None:
    run(lambda args: (1, None, "Error: not logged in"))
    with pytest.raises(FlagsmithAdminError, match="not logged in") as caught:
        _admin().whoami()
    assert caught.value.stderr == "Error: not logged in"


def test_error_on_stderr_raises_even_when_the_exit_code_is_zero(
    run: Callable[..., Recorder],
) -> None:
    # The npm wrapper drops the native binary's exit code.
    stderr = '{"detail":"Not found."}\nError: GET ... returned 404 Not Found\n'
    run(lambda args: (0, None, stderr))
    with pytest.raises(FlagsmithAdminError, match="404"):
        _admin().get_experiment(ENV, 99)


def test_status_lines_on_stderr_are_not_errors(run: Callable[..., Recorder]) -> None:
    run(lambda args: (0, {"id": 5, "status": "running"}, "✓ Done\n"))
    assert _admin().get_experiment(ENV, 5)["status"] == "running"


def test_missing_cli_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flagsmith_admin.shutil, "which", lambda name: None)
    with pytest.raises(FlagsmithAdminError, match="not on PATH"):
        FlagsmithAdmin("second-audience").whoami()


def test_segment_commands(run: Callable[..., Recorder]) -> None:
    rules = {"type": "ALL", "rules": []}

    def respond(args: list[str]) -> tuple[int, Any, str]:
        if args[:2] == ["segment", "list"]:
            return 0, [{"id": 5, "name": "other"}, {"id": 7, "name": "sa-x-rollout"}], ""
        if args[:2] == ["segment", "get"]:
            return 0, {"id": 7, "name": "sa-x-rollout", "rules": rules}, ""
        if args[:2] == ["segment", "create"]:
            return 0, {"id": 8, "name": args[2]}, ""
        if args[:2] == ["flag", "get"]:
            return 0, {"enabled": True, "value": "v", "variants": []}, ""
        return 0, None, ""

    recorder = run(respond)
    admin = _admin()
    assert admin.get_segment("sa-x-rollout") == {"id": 7, "name": "sa-x-rollout", "rules": rules}
    assert admin.get_segment("missing") is None
    assert admin.create_segment("sa-y-rollout", rules, "why")["id"] == 8
    admin.set_segment_override("error_style", 8, '{"a":true}', ["prescriptive"], ENV)
    assert admin.segment_override("error_style", 8, ENV)["value"] == "v"
    calls = [args for args, _ in recorder.calls]
    assert calls[1] == ["segment", "get", "7", *SUFFIX, "--json"]
    assert calls[3] == [
        "segment", "create", "sa-y-rollout", "--rules", json.dumps(rules),
        "--description", "why", *SUFFIX, "--json",
    ]  # fmt: skip
    assert calls[4] == [
        "flag", "update", "error_style", "--segment", "8", "--enable",
        "--value", '{"a":true}', "--type", "string", "--yes", "--weight", "prescriptive=0",
        *SUFFIX, "--environment", ENV, "--json",
    ]  # fmt: skip
    assert calls[5] == [
        "flag", "get", "error_style", "--segment", "8", *SUFFIX, "--environment", ENV, "--json",
    ]  # fmt: skip
