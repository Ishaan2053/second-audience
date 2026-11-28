"""The inventory example server, driven end to end by its scripted MCP client."""

import pytest
from fastmcp import Client

from examples.inventory_server.client import FAILING_CALL, demo
from examples.inventory_server.server import (
    INSUFFICIENT_STOCK,
    build_server,
    second_audience_from_settings,
)
from second_audience.config import Settings
from second_audience.core import (
    PRESCRIPTIVE,
    TASK_COMPLETED,
    TASK_FINISHED,
    NullExperimentClient,
    SecondAudience,
)


async def test_scripted_client_shows_two_responses_for_the_same_failing_call() -> None:
    responses = await demo()
    control, prescriptive = responses["control"], responses["prescriptive"]
    assert control != prescriptive
    assert control["error"] == prescriptive["error"]  # the fact is the same in both arms
    assert control["error"]["code"] == INSUFFICIENT_STOCK
    assert set(control) == {"error"}
    assert prescriptive["suggestion"] == {"warehouse": "WEST", "available": 12}
    assert prescriptive["corrected_call"] == {
        "tool": "reserve_stock",
        "arguments": {**FAILING_CALL, "warehouse": "WEST"},
    }


async def test_session_is_exposed_at_the_error_and_scored_at_finish() -> None:
    experiment = NullExperimentClient(PRESCRIPTIVE, "prescriptive")
    async with Client(build_server(SecondAudience(experiment))) as client:
        started = await client.call_tool("start_order", {})
        assert isinstance(started.structured_content, dict)
        session_id = started.structured_content["session_id"]
        assert experiment.resolved == []  # no exposure before the first styled error
        failed = await client.call_tool("reserve_stock", {"session_id": session_id, **FAILING_CALL})
        assert isinstance(failed.structured_content, dict)
        corrected = failed.structured_content["corrected_call"]["arguments"]
        await client.call_tool("reserve_stock", {"session_id": session_id, **corrected})
        finished = await client.call_tool(
            "finish_order", {"session_id": session_id, "summary": "Reserved 5 from WEST."}
        )
    assert isinstance(finished.structured_content, dict)
    assert finished.structured_content["completed"] is True
    assert experiment.resolved == [session_id]
    assert experiment.tracked == [
        (session_id, TASK_COMPLETED, None),
        (session_id, TASK_FINISHED, 2),
    ]
    assert experiment.flushes == 1


async def test_session_without_a_styled_error_is_never_exposed() -> None:
    experiment = NullExperimentClient(PRESCRIPTIVE, "prescriptive")
    async with Client(build_server(SecondAudience(experiment))) as client:
        started = await client.call_tool("start_order", {})
        assert isinstance(started.structured_content, dict)
        session_id = started.structured_content["session_id"]
        await client.call_tool(
            "reserve_stock",
            {"session_id": session_id, "sku": "SKU-100", "warehouse": "WEST", "quantity": 5},
        )
        await client.call_tool("finish_order", {"session_id": session_id, "summary": "Done."})
    assert experiment.resolved == []
    assert experiment.tracked == []


def test_example_refuses_the_reference_experiment_flag() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sa_example_flag_name="error_style",
    )
    with pytest.raises(ValueError, match="SA_EXAMPLE_FLAG_NAME"):
        second_audience_from_settings(settings)
