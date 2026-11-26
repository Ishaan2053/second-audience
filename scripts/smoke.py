"""Checkpoint A smoke run: scripted MCP sessions against a running booking server, no model.

Start the server first, then run:

    uv run python -m second_audience.booking.server --transport http --port 8000
    uv run python scripts/smoke.py --sessions 40

Each session begins a scenario (cycling through all twelve unless `--scenario` is given),
makes the scenario's first attempt, which hits a styled error, then applies the error's
`corrected_call` when there is one and the scenario's known solution otherwise, and finishes.
So control sessions end correct, and prescriptive sessions end incorrect on trap scenarios.
The table at the end is read from the ledger the server writes (`SA_DATABASE_URL`), and gives
the counts Flagsmith should show. If `SA_LAB_KEY` is set, sessions are sent as lab traffic.
"""

import argparse
import asyncio
from collections import Counter
from typing import Any

from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from second_audience.booking.scenarios import SCENARIOS, Scenario, get_scenario
from second_audience.booking.sessions import AGENT_LABEL_HEADER, LAB_KEY_HEADER
from second_audience.config import get_settings
from second_audience.core.ledger import Ledger, SessionSummary, make_engine

DEFAULT_URL = "http://127.0.0.1:8000/mcp"
AGENT_LABEL = "smoke-script"


async def _call(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool and return its structured payload; protocol or schema errors abort."""
    result = await client.call_tool(tool, arguments)
    if result.is_error or not isinstance(result.structured_content, dict):
        raise RuntimeError(f"{tool} failed: {result.content}")
    return result.structured_content


async def _play(client: Client, scenario: Scenario) -> str:
    """Run one scripted session and return its session ID."""
    started = await _call(client, "begin_task", {"scenario_id": scenario.id})
    session_id: str = started["session_id"]
    error = await _call(client, "book_room", scenario.first_attempt.tool_arguments(session_id))
    if "error" not in error:
        raise RuntimeError(f"{scenario.id}: the first attempt did not fail: {error}")
    corrected = error.get("corrected_call")
    if corrected is not None:
        await _call(client, corrected["tool"], corrected["arguments"])
    else:
        await _call(client, "book_room", scenario.solution.tool_arguments(session_id))
    await _call(client, "finish_task", {"session_id": session_id, "summary": "smoke run"})
    return session_id


async def _run(url: str, scenarios: list[Scenario], headers: dict[str, str]) -> list[str]:
    """Play every scenario in order over one MCP connection."""
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        tools = sorted(tool.name for tool in (await client.list_tools()).tools)
        print(f"connected to {url}; tools: {', '.join(tools)}")
        return [await _play(client, scenario) for scenario in scenarios]


def _table(summaries: list[SessionSummary]) -> str:
    """Markdown table of outcomes and expected Flagsmith counts, by variant."""
    rows = [
        "| Variant | Sessions | Exposed in experiment | Correct | Incorrect | "
        "Expected `task_completed` | Expected `task_wrong_booking` |",
        "|---|---|---|---|---|---|---|",
    ]
    by_variant: dict[str, list[SessionSummary]] = {}
    for summary in summaries:
        by_variant.setdefault(summary.variant or "(none)", []).append(summary)
    for variant, group in sorted(by_variant.items()):
        final = Counter(s.final_state for s in group)
        enrolled = [s for s in group if s.in_experiment and s.outcome != "abandoned"]
        completed = sum(s.final_state == "correct" for s in enrolled)
        wrong = sum(s.final_state == "incorrect" for s in enrolled)
        rows.append(
            f"| {variant} | {len(group)} | {sum(bool(s.in_experiment) for s in group)} | "
            f"{final['correct']} | {final['incorrect']} | {completed} | {wrong} |"
        )
    return "\n".join(rows)


def main() -> None:
    """Parse arguments, run the sessions and print the ledger table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help=f"MCP endpoint (default: SA_SERVER_URL or {DEFAULT_URL})")
    parser.add_argument("--sessions", type=int, default=40)
    parser.add_argument("--scenario", help="run only this scenario ID (default: cycle all)")
    args = parser.parse_args()

    settings = get_settings()
    url = args.url or settings.sa_server_url or DEFAULT_URL
    pool = [get_scenario(args.scenario)] if args.scenario else list(SCENARIOS)
    scenarios = [pool[i % len(pool)] for i in range(args.sessions)]
    headers: dict[str, str] = {}
    if settings.sa_lab_key is not None:
        headers = {
            LAB_KEY_HEADER: settings.sa_lab_key.get_secret_value(),
            AGENT_LABEL_HEADER: AGENT_LABEL,
        }

    session_ids = asyncio.run(_run(url, scenarios, headers))

    summaries = Ledger(make_engine(settings)).session_summaries(session_ids)
    unfinished = [s.session_id for s in summaries if s.outcome is None]
    print(f"\n{len(summaries)} sessions in the ledger ({len(unfinished)} unfinished)")
    print(f"sources: {dict(Counter(s.source for s in summaries))}")
    print(f"outcomes: {dict(Counter(s.outcome for s in summaries))}\n")
    print(_table(summaries))
    print(f"\nfirst session: {session_ids[0]}  last session: {session_ids[-1]}")


if __name__ == "__main__":
    main()
