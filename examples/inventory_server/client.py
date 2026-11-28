"""Scripted client: the same failing call against the example server under two styles.

Run it with `uv run python -m examples.inventory_server.client`. It needs no Flagsmith account:
each run pins the style with a `NullExperimentClient`, so both arms are shown side by side.
"""

import asyncio
import json
from typing import Any

from fastmcp import Client

from examples.inventory_server.server import build_server
from second_audience.core import CONTROL, PRESCRIPTIVE, NullExperimentClient, SecondAudience

STYLES = {"control": CONTROL, "prescriptive": PRESCRIPTIVE}
FAILING_CALL: dict[str, Any] = {"sku": "SKU-100", "warehouse": "EAST", "quantity": 5}


async def failing_call(variant: str) -> dict[str, Any]:
    """Start an order and make the over-sized reservation under one variant's style."""
    sa = SecondAudience(NullExperimentClient(STYLES[variant], variant))
    async with Client(build_server(sa)) as client:
        started = await client.call_tool("start_order", {})
        assert isinstance(started.structured_content, dict)
        session_id = started.structured_content["session_id"]
        result = await client.call_tool("reserve_stock", {"session_id": session_id, **FAILING_CALL})
        assert isinstance(result.structured_content, dict)
        await client.call_tool("finish_order", {"session_id": session_id, "summary": "Demo."})
        return result.structured_content


async def demo() -> dict[str, dict[str, Any]]:
    """The response to the same failing call, per variant."""
    return {variant: await failing_call(variant) for variant in STYLES}


def main() -> None:
    """Print both responses."""
    for variant, response in asyncio.run(demo()).items():
        print(f"--- {variant}")
        print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
