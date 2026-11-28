"""Thin wrapper over the `flagsmith` CLI and `flagsmith api`, the operator's control plane.

The admin credential stays with the CLI's own login; nothing here reads or stores it. Every
call runs the CLI as an argument list (never through a shell), asks for JSON, and raises
`FlagsmithAdminError` with the CLI's stderr when it fails. Commands and payloads follow
`docs/spike-findings.md` and Flagsmith's "set up with a coding agent" guide.
"""

import json
import shutil
import subprocess
from typing import Any


class FlagsmithAdminError(RuntimeError):
    """A CLI call failed. `stderr` is the CLI's own explanation."""

    def __init__(self, command: list[str], stderr: str) -> None:
        """Keep the command (without credentials, which the CLI never takes as args)."""
        self.command = command
        self.stderr = stderr.strip()
        super().__init__(f"flagsmith {' '.join(command)} failed: {self.stderr or 'no output'}")


def _failed(result: subprocess.CompletedProcess[str]) -> bool:
    """Whether a CLI call failed.

    The npm-installed CLI is a Node wrapper that does not pass on the native binary's exit
    code, so errors exit 0 there (docs/spike-findings.md, SA-09). The CLI prefixes every
    error with "Error:" on stderr, which works for both installs.
    """
    return result.returncode != 0 or any(
        line.startswith("Error:") for line in result.stderr.splitlines()
    )


def _items(payload: Any) -> list[dict[str, Any]]:
    """List endpoints return either a bare list or a paginated `{"results": [...]}`."""
    if isinstance(payload, dict):
        payload = payload.get("results", [])
    if not isinstance(payload, list):
        raise TypeError(f"expected a list from Flagsmith, got {type(payload).__name__}")
    return payload


def feature_id_of(experiment: dict[str, Any]) -> int:
    """The experiment's feature ID: requests send an ID, responses return the feature object."""
    feature = experiment["feature"]
    return int(feature["id"] if isinstance(feature, dict) else feature)


class FlagsmithAdmin:
    """Flagsmith management for one project, through the CLI the developer is logged in to."""

    def __init__(self, project: str, executable: str | None = None) -> None:
        """Bind to a project by name or ID; `executable` defaults to `flagsmith` on PATH."""
        self.project = project
        self._executable = executable

    @property
    def executable(self) -> str:
        """Path of the CLI; raises `FlagsmithAdminError` when it is not installed."""
        found = self._executable or shutil.which("flagsmith")
        if found is None:
            raise FlagsmithAdminError(["--version"], "the flagsmith CLI is not on PATH")
        return found

    def _run(self, *args: str, body: Any = None, environment: str | None = None) -> Any:
        """Run one CLI command non-interactively and parse its JSON output."""
        command = [*args, "--project", self.project, "--no-input"]
        if environment is not None:
            command += ["--environment", environment]
        if args[0] != "api":
            command.append("--json")
        if body is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            [self.executable, *command],
            input=json.dumps(body) if body is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if _failed(result):
            raise FlagsmithAdminError(command, result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _api(self, path: str, method: str = "GET", body: Any = None) -> Any:
        """Call the Admin API with the CLI's credentials."""
        args = ["api", path] if method == "GET" else ["api", path, "-X", method]
        return self._run(*args, body=body)

    # --- account and environments ---------------------------------------------------------

    def whoami(self) -> str:
        """The logged-in account's email; raises when the CLI is not logged in."""
        status = self._run("auth", "status")
        if not isinstance(status, dict) or not status.get("email"):
            raise FlagsmithAdminError(["auth", "status"], "not logged in; run `flagsmith login`")
        return str(status["email"])

    def environment_key(self, name: str) -> str:
        """The client-side key of the environment with this name (case-insensitive)."""
        for environment in _items(self._run("environment", "list")):
            if str(environment["name"]).lower() == name.lower():
                return str(environment["api_key"])
        raise FlagsmithAdminError(["environment", "list"], f"no environment named {name!r}")

    def server_keys(self, env: str) -> list[str]:
        """The environment's active server-side SDK keys, for comparison only; never print."""
        keys = _items(self._api(f"api/v1/environments/{env}/api-keys/"))
        return [str(key["key"]) for key in keys if key.get("active")]

    def warehouse_connected(self, env: str) -> bool:
        """Whether the environment has a warehouse connection.

        Existence is the test, not `status`: the managed warehouse stays
        `pending_connection` while it receives and serves events (spike findings, SA-02).
        """
        return bool(_items(self._api(f"api/v1/environments/{env}/warehouse-connections/")))

    # --- the flag ---------------------------------------------------------------------------

    def get_feature(self, name: str) -> dict[str, Any] | None:
        """The project's feature with this name, or `None`."""
        for feature in _items(self._run("feature", "list")):
            if feature.get("name") == name:
                return feature
        return None

    def create_feature(self, name: str, value: str) -> dict[str, Any]:
        """Create an enabled feature whose default value is the control arm; reuse if present."""
        existing = self.get_feature(name)
        if existing is not None:
            return existing
        self._run("feature", "create", name, "--value", value, "--enabled")
        created = self.get_feature(name)
        if created is None:
            raise FlagsmithAdminError(["feature", "create", name], "feature missing after create")
        return created

    def list_variants(self, name: str) -> list[dict[str, Any]]:
        """The feature's multivariate options."""
        return _items(self._run("feature", "variant", "list", name))

    def add_variant(self, name: str, key: str, value: str, env: str) -> dict[str, Any]:
        """Add a keyed variant with weight 0, so only the experiment assigns it; reuse if present.

        Every variation needs a key, or it records no exposures; `control` is reserved for
        the flag's own value (Flagsmith's coding-agent guide).
        """
        for variant in self.list_variants(name):
            if variant.get("key") == key:
                return variant
        self._run(
            "feature", "variant", "add", name,
            "--key", key, "--value", value, "--weight", "0",
            environment=env,
        )  # fmt: skip
        for variant in self.list_variants(name):
            if variant.get("key") == key:
                return variant
        raise FlagsmithAdminError(["feature", "variant", "add", name], "variant missing after add")

    # --- metrics ----------------------------------------------------------------------------

    def list_metrics(self, env: str) -> list[dict[str, Any]]:
        """The environment's experiment metrics."""
        return _items(self._api(f"api/v1/environments/{env}/experiment-metrics/"))

    def create_metric(
        self, env: str, name: str, event: str, aggregation: str, direction: str
    ) -> dict[str, Any]:
        """Create a metric over one event; reuse an existing metric with the same name."""
        for metric in self.list_metrics(env):
            if metric.get("name") == name:
                return metric
        body = {
            "name": name,
            "aggregation": aggregation,
            "direction": direction,
            "definition": {"version": 1, "event": event},
        }
        created: dict[str, Any] = self._api(
            f"api/v1/environments/{env}/experiment-metrics/", "POST", body
        )
        return created

    # --- experiments ------------------------------------------------------------------------

    def list_experiments(self, env: str) -> list[dict[str, Any]]:
        """The environment's experiments, in every status."""
        return _items(self._api(f"api/v1/environments/{env}/experiments/"))

    def get_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
        """One experiment, with its status."""
        experiment: dict[str, Any] = self._api(
            f"api/v1/environments/{env}/experiments/{experiment_id}/"
        )
        return experiment

    def create_experiment(self, env: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an experiment (status `created`); reuse one with the same name and feature."""
        for experiment in self.list_experiments(env):
            if (experiment.get("name"), feature_id_of(experiment)) == (
                payload["name"],
                feature_id_of(payload),
            ):
                return experiment
        created: dict[str, Any] = self._api(
            f"api/v1/environments/{env}/experiments/", "POST", payload
        )
        return created

    def start_experiment(self, env: str, experiment_id: int) -> dict[str, Any]:
        """Start a created experiment. This locks the flag and turns on the rollout."""
        started: dict[str, Any] = self._api(
            f"api/v1/environments/{env}/experiments/{experiment_id}/start/", "POST"
        )
        return started

    def complete_experiment(
        self, env: str, experiment_id: int, rollout_decision: str, rollout_variant: int | None
    ) -> dict[str, Any]:
        """End a running experiment and roll out `control` or a `variant`; irreversible.

        `POST .../complete/` (spike findings, SA-02 question 8): `rollout_variant` is the
        multivariate option ID and is sent only with `rollout_decision="variant"`.
        """
        body: dict[str, Any] = {"rollout_decision": rollout_decision}
        if rollout_variant is not None:
            body["rollout_variant"] = rollout_variant
        completed: dict[str, Any] = self._api(
            f"api/v1/environments/{env}/experiments/{experiment_id}/complete/", "POST", body
        )
        return completed

    def feature_state(self, env: str, feature_id: int) -> dict[str, Any]:
        """The environment's default state of one feature: value, enabled, variant weights."""
        states = _items(self._api(f"api/v1/environments/{env}/featurestates/?feature={feature_id}"))
        defaults = [s for s in states if s.get("identity") is None and not s.get("feature_segment")]
        if len(defaults) != 1:
            raise FlagsmithAdminError(
                ["api", "featurestates"],
                f"expected one environment default for feature {feature_id}, got {len(defaults)}",
            )
        return defaults[0]

    # --- segments (SA-21) ---------------------------------------------------------------------

    def get_segment(self, name: str) -> dict[str, Any] | None:
        """The project's segment with this name, with its rule tree, or `None`."""
        for segment in _items(self._run("segment", "list")):
            if segment.get("name") == name:
                found: dict[str, Any] = self._run("segment", "get", str(segment["id"]))
                return found
        return None

    def create_segment(self, name: str, rules: dict[str, Any], description: str) -> dict[str, Any]:
        """Create a project segment from one rule tree (an object, not a list).

        Segments belong to the project, not an environment; only an override makes one act.
        """
        created: dict[str, Any] = self._run(
            "segment", "create", name, "--rules", json.dumps(rules), "--description", description
        )
        return created

    def set_segment_override(
        self, feature: str, segment_id: int, value: str, variant_keys: list[str], env: str
    ) -> None:
        """Give a segment its own enabled value for a multivariate flag, every variant at 0%.

        At 0% the override serves its own value to every identity in the segment (spike
        findings, SA-21). The CLI creates the override if the segment has none.
        """
        weights = ",".join(f"{key}=0" for key in variant_keys)
        args = ["flag", "update", feature, "--segment", str(segment_id), "--enable"]
        args += ["--value", value, "--type", "string", "--yes"]
        if weights:
            args += ["--weight", weights]
        self._run(*args, environment=env)

    def segment_override(self, feature: str, segment_id: int, env: str) -> dict[str, Any]:
        """A segment override as `flag get --segment` shows it: enabled, value, variants."""
        override: dict[str, Any] = self._run(
            "flag", "get", feature, "--segment", str(segment_id), environment=env
        )
        return override

    def refresh_results(self, env: str, experiment_id: int) -> None:
        """Ask Flagsmith to recompute results; allowed once every 5 minutes."""
        self._api(f"api/v1/environments/{env}/experiments/{experiment_id}/results/refresh/", "POST")

    def get_results(self, env: str, experiment_id: int) -> dict[str, Any]:
        """The latest computed results."""
        results: dict[str, Any] = self._api(
            f"api/v1/environments/{env}/experiments/{experiment_id}/results/"
        )
        return results
