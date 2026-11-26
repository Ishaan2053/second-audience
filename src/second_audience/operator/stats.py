"""The few statistics the report computes itself. Flagsmith's engine supplies the headline."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

Z_95 = 1.959963984540054
"""Two-sided 95% normal quantile."""

SRM_ALPHA = 0.01
"""A sample-ratio-mismatch p-value below this flags the split as unhealthy."""


@dataclass(frozen=True, slots=True)
class Interval:
    """A proportion with its 95% Wilson score interval."""

    successes: int
    n: int
    low: float
    high: float

    @property
    def rate(self) -> float:
        """The observed proportion."""
        return self.successes / self.n


def wilson(successes: int, n: int) -> Interval | None:
    """95% Wilson score interval for `successes` out of `n`; `None` when `n` is 0."""
    if not 0 <= successes <= n:
        raise ValueError(f"need 0 <= successes <= n, got {successes} of {n}")
    if n == 0:
        return None
    p = successes / n
    z2 = Z_95 * Z_95
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z_95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return Interval(successes, n, max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True, slots=True)
class SrmTest:
    """Chi-square goodness of fit of observed arm counts to the configured split."""

    observed: dict[str, int]
    expected: dict[str, float]
    chi_square: float
    p_value: float

    @property
    def mismatch(self) -> bool:
        """Whether the split differs from the configuration beyond chance."""
        return self.p_value < SRM_ALPHA


def srm_test(observed: Mapping[str, int], split_percent: Mapping[str, float]) -> SrmTest | None:
    """Sample-ratio-mismatch test for two arms (one degree of freedom); `None` with no data.

    With one degree of freedom the chi-square tail is `erfc(sqrt(x / 2))`, so no library is
    needed. More arms would need the general incomplete gamma function; the project runs
    two-arm experiments only.
    """
    if set(observed) != set(split_percent) or len(observed) != 2:
        raise ValueError("the SRM test needs exactly two arms, matching the configured split")
    total = sum(observed.values())
    if total == 0:
        return None
    expected = {arm: total * split_percent[arm] / 100 for arm in observed}
    chi_square = sum((observed[arm] - expected[arm]) ** 2 / expected[arm] for arm in observed)
    return SrmTest(dict(observed), expected, chi_square, math.erfc(math.sqrt(chi_square / 2)))


def stopping_rule_met(
    exposed: Mapping[str, int], arms: list[str], min_per_arm: int, hard_end: datetime, now: datetime
) -> bool:
    """Pre-registered: every arm has `min_per_arm` exposed sessions, or the hard end has passed."""
    return now >= hard_end or all(exposed.get(arm, 0) >= min_per_arm for arm in arms)
