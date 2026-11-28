"""Core middleware: variant assignment, exposure and conversion events, session ledger.

The public API for using Second Audience on any MCP server is re-exported here.
"""

from second_audience.core.experiment import (
    TASK_COMPLETED,
    TASK_FINISHED,
    TASK_WRONG_BOOKING,
    ExperimentClient,
    FlagsmithExperimentClient,
    NullExperimentClient,
)
from second_audience.core.ledger import Ledger
from second_audience.core.middleware import SecondAudience, experiment_client_from_settings
from second_audience.core.styles import CONTROL, PRESCRIPTIVE, ErrorStyle

__all__ = [
    "CONTROL",
    "PRESCRIPTIVE",
    "TASK_COMPLETED",
    "TASK_FINISHED",
    "TASK_WRONG_BOOKING",
    "ErrorStyle",
    "ExperimentClient",
    "FlagsmithExperimentClient",
    "Ledger",
    "NullExperimentClient",
    "SecondAudience",
    "experiment_client_from_settings",
]
