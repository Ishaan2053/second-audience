"""Wilson interval, the SRM chi-square test and the stopping rule."""

from datetime import UTC, datetime, timedelta

import pytest

from second_audience.operator.stats import srm_test, stopping_rule_met, wilson


def test_wilson_matches_reference_values() -> None:
    # Reference: Wilson score interval, z = 1.96 (e.g. statsmodels proportion_confint).
    half = wilson(5, 10)
    assert half is not None
    assert half.rate == 0.5
    assert half.low == pytest.approx(0.2366, abs=1e-4)
    assert half.high == pytest.approx(0.7634, abs=1e-4)
    zero = wilson(0, 10)
    assert zero is not None
    assert zero.low == 0.0
    assert zero.high == pytest.approx(0.2775, abs=1e-4)
    full = wilson(23, 23)
    assert full is not None
    assert full.high == 1.0
    assert full.low == pytest.approx(0.8569, abs=1e-4)


def test_wilson_with_no_data_and_bad_input() -> None:
    assert wilson(0, 0) is None
    with pytest.raises(ValueError, match="successes"):
        wilson(3, 2)


def test_srm_chi_square_one_degree_of_freedom() -> None:
    balanced = srm_test({"control": 50, "prescriptive": 50}, {"control": 50, "prescriptive": 50})
    assert balanced is not None
    assert balanced.chi_square == 0
    assert balanced.p_value == pytest.approx(1.0)
    assert not balanced.mismatch
    # 60 / 40 of 100 against 50 / 50: chi-square = 4, p = 0.0455.
    skewed = srm_test({"control": 60, "prescriptive": 40}, {"control": 50, "prescriptive": 50})
    assert skewed is not None
    assert skewed.chi_square == pytest.approx(4.0)
    assert skewed.p_value == pytest.approx(0.0455, abs=1e-4)
    assert not skewed.mismatch
    severe = srm_test({"control": 70, "prescriptive": 30}, {"control": 50, "prescriptive": 50})
    assert severe is not None
    assert severe.chi_square == pytest.approx(16.0)
    assert severe.mismatch


def test_srm_respects_an_uneven_configured_split() -> None:
    result = srm_test({"control": 80, "prescriptive": 20}, {"control": 80, "prescriptive": 20})
    assert result is not None
    assert result.expected == {"control": 80.0, "prescriptive": 20.0}
    assert result.chi_square == 0


def test_srm_needs_two_matching_arms_and_data() -> None:
    assert srm_test({"control": 0, "b": 0}, {"control": 50, "b": 50}) is None
    with pytest.raises(ValueError, match="two arms"):
        srm_test({"control": 1, "b": 1, "c": 1}, {"control": 40, "b": 30, "c": 30})
    with pytest.raises(ValueError, match="two arms"):
        srm_test({"control": 1, "b": 1}, {"control": 50, "x": 50})


def test_stopping_rule_needs_every_arm_or_the_hard_end() -> None:
    end = datetime(2026, 11, 29, 6, 30, tzinfo=UTC)
    before = end - timedelta(hours=1)
    arms = ["control", "prescriptive"]
    assert not stopping_rule_met({"control": 75, "prescriptive": 74}, arms, 75, end, before)
    assert stopping_rule_met({"control": 75, "prescriptive": 80}, arms, 75, end, before)
    assert not stopping_rule_met({"control": 75}, arms, 75, end, before)
    assert stopping_rule_met({"control": 1, "prescriptive": 0}, arms, 75, end, end)
