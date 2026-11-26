"""Error-response styles: the experiment's treatment, parsed from the flag value.

The flag value is a JSON object of booleans. Treating the style as configuration rather
than a variant name means a new challenger can be rolled out without a redeploy.
"""

import json
import logging
from typing import Self

from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator

logger = logging.getLogger(__name__)


class ErrorStyle(BaseModel):
    """Which optional fields a styled error carries on top of the plain fact."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    suggest_alternative: bool = False
    corrected_call: bool = False
    caution_note: bool = False

    @field_validator("corrected_call")
    @classmethod
    def _needs_suggestion(cls, value: bool, info: ValidationInfo) -> bool:
        """A corrected call is built from the suggestion, so it is off without one."""
        return value and info.data.get("suggest_alternative") is True

    @classmethod
    def from_flag_value(cls, value: str | None) -> Self:
        """Parse a flag value; missing or invalid input yields the control style."""
        if not value:
            logger.warning("error style flag value is empty; using the control style")
            return cls()
        try:
            return cls.model_validate_json(value)
        except ValidationError:
            logger.warning("error style flag value is invalid; using the control style")
            return cls()


CONTROL = ErrorStyle()
PRESCRIPTIVE = ErrorStyle(suggest_alternative=True, corrected_call=True)

# Canonical flag values per arm, as stored on the Flagsmith flag. Key = variant key.
ARM_FLAG_VALUES: dict[str, str] = {
    "control": json.dumps(CONTROL.model_dump()),
    "prescriptive": json.dumps(PRESCRIPTIVE.model_dump()),
}
