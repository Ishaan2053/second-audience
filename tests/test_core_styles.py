"""ErrorStyle parsing from flag values, and the canonical per-arm flag values."""

import json
import logging

import pytest

from second_audience.core.styles import ARM_FLAG_VALUES, CONTROL, PRESCRIPTIVE, ErrorStyle


def test_canonical_flag_values_match_the_architecture() -> None:
    assert json.loads(ARM_FLAG_VALUES["control"]) == {
        "suggest_alternative": False,
        "corrected_call": False,
        "caution_note": False,
    }
    assert json.loads(ARM_FLAG_VALUES["prescriptive"]) == {
        "suggest_alternative": True,
        "corrected_call": True,
        "caution_note": False,
    }


@pytest.mark.parametrize(("arm", "style"), [("control", CONTROL), ("prescriptive", PRESCRIPTIVE)])
def test_canonical_values_round_trip(arm: str, style: ErrorStyle) -> None:
    assert ErrorStyle.from_flag_value(ARM_FLAG_VALUES[arm]) == style


def test_all_fields_parse() -> None:
    value = '{"suggest_alternative": true, "corrected_call": true, "caution_note": true}'
    assert ErrorStyle.from_flag_value(value) == ErrorStyle(
        suggest_alternative=True, corrected_call=True, caution_note=True
    )


def test_missing_keys_default_to_false() -> None:
    assert ErrorStyle.from_flag_value('{"suggest_alternative": true}') == ErrorStyle(
        suggest_alternative=True
    )


def test_unknown_keys_are_ignored() -> None:
    value = '{"suggest_alternative": true, "tone": "friendly"}'
    assert ErrorStyle.from_flag_value(value) == ErrorStyle(suggest_alternative=True)


def test_corrected_call_without_suggestion_is_off() -> None:
    style = ErrorStyle.from_flag_value('{"corrected_call": true, "caution_note": true}')
    assert style == ErrorStyle(caution_note=True)
    assert ErrorStyle(corrected_call=True).corrected_call is False


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not json",
        "[true]",
        '"prescriptive"',
        "null",
        '{"suggest_alternative": "yes"}',
        '{"suggest_alternative": 1}',
        '{"suggest_alternative": true',
    ],
)
def test_missing_or_invalid_values_yield_control_with_a_warning(
    value: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="second_audience.core.styles"):
        assert ErrorStyle.from_flag_value(value) == CONTROL
    assert "control style" in caplog.text


def test_style_is_immutable() -> None:
    with pytest.raises(ValueError, match="frozen"):
        PRESCRIPTIVE.suggest_alternative = False  # type: ignore[misc]
