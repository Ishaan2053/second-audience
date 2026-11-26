"""SA-02 throwaway spike: send exposures and events for synthetic identities to Flagsmith.

Run against the `development` environment only, with a running throwaway experiment:

    uv run python scripts/spike_flagsmith.py --flag spike_style

Even-numbered identities use the eager path (`get_experiment_flag`); odd-numbered ones use
the lazy path (resolve without exposure, then `track_exposure_event`). Every identity gets
a `traffic` trait (`lab` or `field`) to test whether results can be segmented by trait.
The environment key comes from `Settings` and is never printed.
"""

import argparse
import random
import time
import uuid
from collections import Counter

from flagsmith import Flagsmith
from flagsmith.models import Flag

from second_audience.config import get_settings

CONVERSION_EVENT = "spike_converted"
GUARDRAIL_EVENT = "spike_guardrail"
# Different true rates per arm so a lift is visible in the results.
CONVERSION_RATE = {"control": 0.3, "variant": 0.6}
GUARDRAIL_RATE = 0.1


def _client() -> Flagsmith:
    """Build an events-enabled client from the project settings."""
    settings = get_settings()
    if settings.flagsmith_environment_key is None:
        raise SystemExit("FLAGSMITH_ENVIRONMENT_KEY is not set in .env")
    return Flagsmith(
        environment_key=settings.flagsmith_environment_key.get_secret_value(),
        api_url=settings.flagsmith_api_url,
        enable_events=True,
    )


def _resolve(client: Flagsmith, flag_name: str, identifier: str, traffic: str, lazy: bool) -> Flag:
    """Resolve the flag and record the exposure via the eager or the lazy path."""
    traits = {"traffic": traffic}
    if lazy:
        flag = client.get_identity_flags(identifier, traits=traits).get_flag(flag_name)
    else:
        flag = client.get_experiment_flag(flag_name, identifier=identifier, traits=traits)
    if not isinstance(flag, Flag):
        raise SystemExit(f"Flag {flag_name!r} not found in this environment")
    if lazy and flag.experiment and flag.experiment.in_experiment and flag.variant:
        client.track_exposure_event(
            flag_name,
            identifier=identifier,
            value=flag.variant,
            traits=traits,
            metadata={"experiment_id": flag.experiment.id},
        )
    return flag


def main() -> None:
    """Send the synthetic traffic and print a summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flag", required=True, help="throwaway multivariate flag name")
    parser.add_argument("--count", type=int, default=40, help="number of identities")
    parser.add_argument("--seed", type=int, default=2)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    run_id = uuid.uuid4().hex[:8]
    client = _client()
    exposures: Counter[str] = Counter()
    conversions: Counter[str] = Counter()
    guardrails: Counter[str] = Counter()
    sample: Flag | None = None

    started = time.time()
    for i in range(args.count):
        identifier = f"spike-{run_id}-{i:03d}"
        traffic = "lab" if (i // 2) % 2 else "field"  # both paths get both trait values
        flag = _resolve(client, args.flag, identifier, traffic, lazy=bool(i % 2))
        sample = sample or flag
        if not (flag.experiment and flag.experiment.in_experiment and flag.variant):
            exposures["not_in_experiment"] += 1
            continue
        arm = flag.variant
        exposures[arm] += 1
        rate = CONVERSION_RATE["control" if arm == "control" else "variant"]
        if rng.random() < rate:
            client.track_event(CONVERSION_EVENT, identifier=identifier)
            conversions[arm] += 1
        if rng.random() < GUARDRAIL_RATE:
            client.track_event(GUARDRAIL_EVENT, identifier=identifier)
            guardrails[arm] += 1

    client.flush_events()
    client.close()

    print(f"run_id={run_id} identities={args.count} sent_at={time.strftime('%H:%M:%S')}")
    print(f"elapsed_s={time.time() - started:.1f}")
    print(f"exposures={dict(exposures)}")
    print(f"conversions={dict(conversions)}")
    print(f"guardrails={dict(guardrails)}")
    if sample is not None:
        value = sample.value
        print(f"sample value type={type(value).__name__} length={len(str(value))}")
        print(f"sample variant={sample.variant} reason={sample.reason}")
        print(f"sample experiment={sample.experiment}")


if __name__ == "__main__":
    main()
