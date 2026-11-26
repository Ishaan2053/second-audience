"""Lab runner: drives planned agent sessions against the booking server on Gemini models.

The runner is deliberately neutral. It gives the model the brief and the server's tools,
relays tool calls and stops; it never learns the variant and treats every session the same.

    uv run python -m second_audience.lab.runner plan --replicates 2
    uv run python -m second_audience.lab.runner run
    uv run python -m second_audience.lab.runner status
"""

import argparse
import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from second_audience.config import Settings, get_settings
from second_audience.core.ledger import ClaimedRun, Ledger, RequestStatus, make_engine
from second_audience.lab.models import (
    GeminiModelClient,
    Message,
    ModelClient,
    ModelTurn,
    QuotaExhaustedError,
    RetryLaterError,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)
from second_audience.lab.plan import plan_runs

logger = logging.getLogger(__name__)

BEGIN_TASK = "begin_task"
FINISH_TASK = "finish_task"
MAX_MODEL_TURNS = 15
MAX_RATE_LIMIT_RETRIES = 6
MAX_BACKOFF_SECONDS = 60.0
MAX_CONSECUTIVE_FAILURES = 3
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")
"""Gemini daily quotas reset at midnight Pacific time (docs/spike-findings.md)."""

LAB_KEY_HEADER = "X-SA-Lab-Key"
AGENT_LABEL_HEADER = "X-SA-Agent-Label"

SessionOpener = Callable[[str], AbstractAsyncContextManager[Client[Any]]]
"""Opens a connected MCP client for a session run by the named model."""

Sleep = Callable[[float], Awaitable[None]]


def last_quota_reset(now: datetime) -> datetime:
    """The most recent midnight Pacific at or before `now`, in UTC."""
    local = now.astimezone(QUOTA_TIMEZONE)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def next_quota_reset(now: datetime) -> datetime:
    """The next midnight Pacific after `now`, in UTC."""
    local = now.astimezone(QUOTA_TIMEZONE)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=QUOTA_TIMEZONE).astimezone(
        UTC
    )


def task_message(brief: str, session_id: str, max_tool_calls: int) -> str:
    """The user message every session starts with: the brief, the session_id and the budget."""
    return (
        f"{brief}\n\nYour session_id is {session_id}. Pass it to every tool call. "
        f"You can make at most {max_tool_calls} tool calls."
    )


def http_session_opener(url: str, lab_key: str) -> SessionOpener:
    """Open sessions over streamable HTTP, marked as lab traffic and labelled by model."""

    def open_session(model: str) -> AbstractAsyncContextManager[Client[Any]]:
        headers = {LAB_KEY_HEADER: lab_key, AGENT_LABEL_HEADER: model}
        return Client(StreamableHttpTransport(url, headers=headers))

    return open_session


class Pacer:
    """Spaces one model's requests at least `60 / per_minute` seconds apart."""

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Pace at `per_minute` requests per minute."""
        self._interval = 60.0 / per_minute
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait until the next request slot, then reserve it."""
        async with self._lock:
            now = self._clock()
            if now < self._next:
                await self._sleep(self._next - now)
                now = self._next
            self._next = now + self._interval


@dataclass(frozen=True, slots=True)
class SessionResult:
    """What one agent session did, as the runner saw it (it never sees the variant)."""

    session_id: str
    outcome: str
    model_calls: int
    tool_calls: int
    finished_by_model: bool
    model_version: str | None


async def _call_tool(client: Client[Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool; return its structured payload, or its error text as a payload."""
    result = await client.call_tool(name, arguments, raise_on_error=False)
    if isinstance(result.structured_content, dict) and not result.is_error:
        return result.structured_content
    text = " ".join(block.text for block in result.content if hasattr(block, "text"))
    return {"error": text} if result.is_error else {"result": text}


async def run_session(
    client: Client[Any],
    scenario_id: str,
    request: Callable[[Sequence[Message], Sequence[ToolSpec]], Awaitable[ModelTurn]],
) -> SessionResult:
    """Run one session: begin the task, loop model turns over the tools, then finish."""
    listed = await client.list_tools()
    tools = [
        ToolSpec(tool.name, tool.description or "", dict(tool.input_schema))
        for tool in listed
        if tool.name != BEGIN_TASK  # the runner begins the task; the model must not
    ]
    offered = {tool.name for tool in tools}
    begun = await _call_tool(client, BEGIN_TASK, {"scenario_id": scenario_id})
    session_id = str(begun["session_id"])
    messages: list[Message] = [
        UserMessage(task_message(begun["brief"], session_id, begun["max_tool_calls"]))
    ]
    model_calls = tool_calls = 0
    model_version: str | None = None
    finish: dict[str, Any] | None = None
    while finish is None and model_calls < MAX_MODEL_TURNS:
        turn = await request(messages, tools)
        model_calls += 1
        model_version = turn.model_version or model_version
        messages.append(turn)
        if not turn.tool_calls:
            break
        for call in turn.tool_calls:
            result = await _execute(client, call, offered)
            tool_calls += 1
            if call.name == FINISH_TASK and result.get("finished"):
                finish = result
                break
            messages.append(ToolResult(call, result))
    finished_by_model = finish is not None
    if finish is None:
        reason = "turn limit reached" if model_calls >= MAX_MODEL_TURNS else "model stopped"
        finish = await _call_tool(
            client,
            FINISH_TASK,
            {"session_id": session_id, "summary": f"Closed by the lab runner: {reason}."},
        )
    return SessionResult(
        session_id,
        str(finish.get("outcome", "unknown")),
        model_calls,
        tool_calls,
        finished_by_model,
        model_version,
    )


async def _execute(client: Client[Any], call: ToolCall, offered: set[str]) -> dict[str, Any]:
    """Run one model tool call through MCP; tools the model was not offered are refused."""
    if call.name not in offered:
        return {"error": f"Unknown tool {call.name!r}."}
    return await _call_tool(client, call.name, call.arguments)


@dataclass(slots=True)
class RunSummary:
    """What one `run` invocation did."""

    done: int = 0
    failed: int = 0
    requeued: int = 0
    parked: dict[str, datetime] = field(default_factory=dict)
    stopped_early: bool = False


class LabRunner:
    """Claims planned runs per model and executes them, with pacing, parking and resume."""

    def __init__(
        self,
        ledger: Ledger,
        clients: Mapping[str, ModelClient],
        open_session: SessionOpener,
        *,
        requests_per_minute: int,
        requests_per_day: int,
        concurrency: int = 1,
        stale_after: timedelta = timedelta(minutes=10),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Bind the runner to the ledger, one client per model and a session opener."""
        if not 1 <= concurrency <= 3:
            raise ValueError("concurrency must be between 1 and 3")
        self._ledger = ledger
        self._clients = dict(clients)
        self._open_session = open_session
        self._requests_per_day = requests_per_day
        self._concurrency = concurrency
        self._stale_after = stale_after
        self._now = now
        self._sleep = sleep
        self._pacers = {name: Pacer(requests_per_minute, sleep=sleep) for name in clients}
        self._parked: dict[str, datetime] = {}
        self._drained: set[str] = set()
        self._turn = 0
        self._consecutive_failures = 0
        self._summary = RunSummary()

    async def run(self) -> RunSummary:
        """Run planned runs until none are left, every model is parked, or failures pile up."""
        requeued = self._ledger.requeue_stale_runs(self._now() - self._stale_after)
        if requeued:
            logger.info("requeued %d runs left running by an earlier runner", requeued)
        await asyncio.gather(*(self._worker() for _ in range(self._concurrency)))
        self._summary.parked = dict(self._parked)
        return self._summary

    async def _worker(self) -> None:
        """Claim and execute runs, round-robin across available models."""
        while self._consecutive_failures < MAX_CONSECUTIVE_FAILURES:
            claimed = self._claim()
            if claimed is None:
                return
            await self._execute(claimed)
        self._summary.stopped_early = True

    def _claim(self) -> ClaimedRun | None:
        """Claim the next run from the next model that is neither parked nor drained."""
        names = list(self._clients)
        for _ in range(len(names)):
            model = names[self._turn % len(names)]
            self._turn += 1
            if model in self._drained or self._is_parked(model):
                continue
            used = self._ledger.model_requests_since(last_quota_reset(self._now())).get(model, 0)
            if used >= self._requests_per_day:
                self._park(model, f"local daily budget of {self._requests_per_day} reached")
                continue
            claimed = self._ledger.claim_next_run(model)
            if claimed is None:
                self._drained.add(model)
                continue
            return claimed
        return None

    def _is_parked(self, model: str) -> bool:
        """Whether the model is parked until a quota reset that has not happened yet."""
        until = self._parked.get(model)
        return until is not None and self._now() < until

    def _park(self, model: str, reason: str) -> None:
        """Park the model until the next daily quota reset."""
        until = next_quota_reset(self._now())
        self._parked[model] = until
        logger.warning("parked %s until %s: %s", model, until.isoformat(), reason)

    async def _execute(self, run: ClaimedRun) -> None:
        """Execute one claimed run and record how it ended."""
        client = self._clients[run.model]

        async def request(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
            return await self._request(client, run.run_id, messages, tools)

        try:
            async with self._open_session(run.model) as mcp:
                result = await run_session(mcp, run.scenario_id, request)
        except QuotaExhaustedError as error:
            # The orphan session is left for the server's idle expiry (outcome `abandoned`).
            self._ledger.requeue_run(run.run_id)
            self._summary.requeued += 1
            self._park(run.model, str(error))
            return
        except asyncio.CancelledError:
            self._ledger.requeue_run(run.run_id)
            logger.warning("interrupted: run %s returned to planned", run.run_id)
            raise
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            self._ledger.fail_run(run.run_id, reason[:2000])
            self._summary.failed += 1
            self._consecutive_failures += 1
            logger.error("run %s failed: %s", run.run_id, reason)
            return
        self._ledger.complete_run(run.run_id, result.session_id)
        self._summary.done += 1
        self._consecutive_failures = 0
        logger.info(
            "run %s done: model=%s model_version=%s sdk=%s scenario=%s replicate=%d "
            "session=%s outcome=%s model_calls=%d tool_calls=%d finished_by=%s",
            run.run_id,
            run.model,
            result.model_version,
            client.sdk_version,
            run.scenario_id,
            run.replicate,
            result.session_id,
            result.outcome,
            result.model_calls,
            result.tool_calls,
            "model" if result.finished_by_model else "runner",
        )

    async def _request(
        self,
        client: ModelClient,
        run_id: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        """Send one model turn, paced; on a retry-later error, back off and retry the same turn."""
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            await self._pacers[client.name].wait()
            try:
                turn = await client.step(messages, tools)
            except RetryLaterError as error:
                self._ledger.record_model_request(client.name, run_id, RequestStatus.RETRY_LATER)
                delay = error.retry_after or min(MAX_BACKOFF_SECONDS, 5.0 * 2**attempt)
                logger.info("%s: %s; retrying in %.0fs", client.name, error, delay)
                await self._sleep(delay)
                continue
            except QuotaExhaustedError:
                self._ledger.record_model_request(
                    client.name, run_id, RequestStatus.QUOTA_EXHAUSTED
                )
                raise
            except Exception:
                self._ledger.record_model_request(client.name, run_id, RequestStatus.ERROR)
                raise
            self._ledger.record_model_request(client.name, run_id, RequestStatus.OK)
            return turn
        raise RuntimeError(f"still refused after {MAX_RATE_LIMIT_RETRIES} retries")


def _require(value: str | None, name: str) -> str:
    """Return a required setting or exit with a clear message."""
    if not value:
        raise SystemExit(f"{name} must be set for the lab runner (see .env.example)")
    return value


def _models(settings: Settings) -> list[str]:
    """The configured lab models, or exit if none are set."""
    if not settings.sa_lab_models:
        raise SystemExit("SA_LAB_MODELS must list at least one model (see .env.example)")
    return settings.sa_lab_models


def _cmd_plan(ledger: Ledger, settings: Settings, replicates: int, seed: int | None) -> None:
    """Add replicates to the plan and print what was added."""
    result = plan_runs(ledger, _models(settings), replicates, seed or secrets.randbelow(2**31))
    print(f"added {result.added} runs, seed {result.seed}")
    for model, (first, last) in result.replicates.items():
        print(f"  {model}: replicates {first}-{last}")


def _cmd_run(ledger: Ledger, settings: Settings, concurrency: int | None) -> None:
    """Execute planned runs until done or parked."""
    api_key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    lab_key = settings.sa_lab_key.get_secret_value() if settings.sa_lab_key else None
    runner = LabRunner(
        ledger,
        {
            name: GeminiModelClient(name, _require(api_key, "GEMINI_API_KEY"))
            for name in _models(settings)
        },
        http_session_opener(
            _require(settings.sa_server_url, "SA_SERVER_URL"), _require(lab_key, "SA_LAB_KEY")
        ),
        requests_per_minute=settings.sa_lab_requests_per_minute,
        requests_per_day=settings.sa_lab_requests_per_day,
        concurrency=concurrency or settings.sa_lab_concurrency,
        stale_after=timedelta(seconds=settings.sa_session_idle_seconds),
    )
    try:
        summary = asyncio.run(runner.run())
    except KeyboardInterrupt:
        print("interrupted; in-flight runs were returned to planned")
        return
    print(f"done {summary.done}, failed {summary.failed}, requeued {summary.requeued}")
    for model, until in summary.parked.items():
        print(f"  {model} parked until {until.isoformat()}")
    if summary.stopped_early:
        print(f"stopped after {MAX_CONSECUTIVE_FAILURES} consecutive failures")


def _cmd_status(ledger: Ledger, settings: Settings) -> None:
    """Print run counts per model and status, and requests used since the last quota reset."""
    reset = last_quota_reset(datetime.now(UTC))
    counts = ledger.run_counts()
    used = ledger.model_requests_since(reset)
    per_run = ledger.requests_per_done_run()
    statuses = ("planned", "running", "done", "failed")
    print(f"{'model':<28}" + "".join(f"{s:>9}" for s in statuses) + f"{'req today':>11}")
    for model in sorted(set(counts) | set(used)):
        row = counts.get(model, {})
        print(
            f"{model:<28}"
            + "".join(f"{row.get(s, 0):>9}" for s in statuses)
            + f"{used.get(model, 0):>11}"
        )
    print(
        f"requests today are counted since {reset.isoformat()} (midnight Pacific); "
        f"local daily budget {settings.sa_lab_requests_per_day} per model"
    )
    for model, average in sorted(per_run.items()):
        sessions = settings.sa_lab_requests_per_day / average if average else 0.0
        print(f"  {model}: {average:.1f} requests per done run, ~{sessions:.0f} sessions per day")


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point: `plan`, `run` and `status`."""
    parser = argparse.ArgumentParser(description="Second Audience lab runner (Gemini).")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="add replicates of every scenario per model")
    plan.add_argument("--replicates", type=int, required=True)
    plan.add_argument("--seed", type=int, help="shuffle seed; random and printed if omitted")
    run = commands.add_parser("run", help="execute planned runs")
    run.add_argument("--concurrency", type=int, choices=[1, 2, 3])
    commands.add_parser("status", help="show run counts and requests used today")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # The HTTP clients (httpx2 under the MCP and Gemini SDKs) log every request at INFO;
    # keep the run log to one line per session.
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    settings = get_settings()
    ledger = Ledger(make_engine(settings))
    if args.command == "plan":
        _cmd_plan(ledger, settings, args.replicates, args.seed)
    elif args.command == "run":
        _cmd_run(ledger, settings, args.concurrency)
    else:
        _cmd_status(ledger, settings)


if __name__ == "__main__":
    main()
