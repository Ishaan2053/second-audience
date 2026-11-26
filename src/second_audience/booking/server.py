"""The reference room-booking MCP server: seven tools over streamable HTTP or stdio.

The tool schema and descriptions are identical in both experiment arms. Only the optional
fields a styled error carries differ, and those are decided in `SessionManager`.

Run it with `uv run python -m second_audience.booking.server --transport http --port 8000`
(MCP at `/mcp`) or `--transport stdio`.
"""

import argparse
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from second_audience.booking.sessions import SessionManager, identify
from second_audience.booking.suggestions import BOOK_ROOM, RESCHEDULE_BOOKING
from second_audience.booking.world import Booking, BookingRequest, Room, format_time, parse_time
from second_audience.config import Settings, get_settings
from second_audience.core.experiment import (
    ExperimentClient,
    FlagsmithExperimentClient,
    NullExperimentClient,
)
from second_audience.core.ledger import Ledger, make_engine

logger = logging.getLogger(__name__)

SERVER_NAME = "second-audience-booking"
MCP_PATH = "/mcp"
MAX_SWEEP_SECONDS = 60.0

INSTRUCTIONS = (
    "A room-booking service for a fictional office with six rooms, open Monday 2027-03-15 to "
    "Friday 2027-03-19. Call begin_task first to get a task and a session_id, use the other "
    "tools to complete the task, then call finish_task."
)

# Strict schema. The literal values mirror `world.ROOMS`, `world.WEEK` and the scenario IDs;
# tests check that they stay in sync.
RoomId = Literal["R1", "R2", "R3", "R4", "R5", "R6"]
IsoDate = Literal["2027-03-15", "2027-03-16", "2027-03-17", "2027-03-18", "2027-03-19"]
ScenarioId = Literal[
    "s01", "s02", "s03", "s04", "s05", "s06", "s07", "s08", "s09", "s10", "s11", "s12"
]

SessionIdArg = Annotated[
    str, Field(min_length=1, max_length=64, description="The session_id from begin_task.")
]
RoomIdArg = Annotated[RoomId, Field(description="Room ID.")]
DateArg = Annotated[IsoDate, Field(description="Date as YYYY-MM-DD.")]
TimeArg = Annotated[
    str,
    Field(
        pattern=r"^([01][0-9]|2[0-3]):[03]0$",
        description="Local time as HH:MM, on 30-minute steps.",
    ),
]
AttendeesArg = Annotated[int, Field(ge=1, le=20, description="Number of people attending.")]
TitleArg = Annotated[str, Field(min_length=1, max_length=200, description="Meeting title.")]
BookingIdArg = Annotated[
    str, Field(min_length=1, max_length=16, description="Booking ID, as returned by book_room.")
]
SummaryArg = Annotated[str, Field(max_length=2000, description="A short summary of what you did.")]


def room_payload(room: Room) -> dict[str, Any]:
    """A room as response JSON."""
    return {
        "room_id": room.room_id,
        "capacity": room.capacity,
        "features": sorted(room.features),
        "opens": format_time(room.opens),
        "closes": format_time(room.closes),
    }


def booking_payload(booking: Booking) -> dict[str, Any]:
    """A booking as response JSON. Who created it is not shown."""
    return {
        "booking_id": booking.booking_id,
        "room_id": booking.room_id,
        "date": booking.date.isoformat(),
        "start": format_time(booking.start),
        "end": format_time(booking.end),
        "attendees": booking.attendees,
        "title": booking.title,
        "status": booking.status,
    }


def _client_name(ctx: Context) -> str | None:
    """The MCP client's self-reported name from the initialize handshake, if any."""
    params = ctx.session.client_params
    return params.client_info.name if params is not None else None


async def _sweep_idle(manager: SessionManager, interval: float) -> None:
    """Expire idle sessions every `interval` seconds until cancelled."""
    while True:
        await asyncio.sleep(interval)
        try:
            expired = await asyncio.to_thread(manager.expire_idle)
        except Exception:
            logger.exception("idle session sweep failed")
            continue
        if expired:
            logger.info("expired %d idle sessions", expired)


def build_server(manager: SessionManager, settings: Settings) -> FastMCP:
    """Create the FastMCP app with the seven booking tools, bound to `manager`."""
    lab_key = settings.sa_lab_key.get_secret_value() if settings.sa_lab_key else None
    sweep_interval = min(MAX_SWEEP_SECONDS, float(settings.sa_session_idle_seconds))

    @lifespan
    async def session_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        """Sweep idle sessions while serving; on stop, abandon open sessions and flush."""
        sweeper = asyncio.create_task(_sweep_idle(manager, sweep_interval))
        try:
            yield {}
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            await asyncio.to_thread(manager.close)

    mcp = FastMCP(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        lifespan=session_lifespan,
        strict_input_validation=True,
        mask_error_details=True,
    )

    @mcp.tool
    def begin_task(ctx: Context, scenario_id: ScenarioId | None = None) -> dict[str, Any]:
        """Start a booking task. Returns a session_id, the task brief and the tool-call budget."""
        identity = identify(get_http_headers(), _client_name(ctx), lab_key)
        return manager.begin(scenario_id, identity)

    @mcp.tool
    def list_rooms(session_id: SessionIdArg) -> dict[str, Any]:
        """List every room with its capacity, equipment and opening hours."""
        return manager.call(
            session_id,
            "list_rooms",
            {"session_id": session_id},
            lambda world: {"rooms": [room_payload(room) for room in world.list_rooms()]},
        )

    @mcp.tool
    def check_availability(
        session_id: SessionIdArg, room_id: RoomIdArg, date: DateArg
    ) -> dict[str, Any]:
        """List the free time slots of one room on one date."""
        day = _parse_date(date)
        return manager.call(
            session_id,
            "check_availability",
            {"session_id": session_id, "room_id": room_id, "date": date},
            lambda world: {
                "room_id": room_id,
                "date": date,
                "free_slots": [
                    {"start": format_time(start), "end": format_time(end)}
                    for start, end in world.free_slots(room_id, day)
                ],
            },
        )

    @mcp.tool
    def book_room(
        session_id: SessionIdArg,
        room_id: RoomIdArg,
        date: DateArg,
        start: TimeArg,
        end: TimeArg,
        attendees: AttendeesArg,
        title: TitleArg,
    ) -> dict[str, Any]:
        """Book a room for a meeting."""
        request = BookingRequest(
            room_id=room_id,
            date=_parse_date(date),
            start=parse_time(start),
            end=parse_time(end),
            attendees=attendees,
            title=title,
        )
        return manager.call(
            session_id,
            BOOK_ROOM,
            {
                "session_id": session_id,
                "room_id": room_id,
                "date": date,
                "start": start,
                "end": end,
                "attendees": attendees,
                "title": title,
            },
            lambda world: {"booking": booking_payload(world.book(request))},
        )

    @mcp.tool
    def reschedule_booking(
        session_id: SessionIdArg,
        booking_id: BookingIdArg,
        date: DateArg,
        start: TimeArg,
        end: TimeArg,
    ) -> dict[str, Any]:
        """Move a booking to a new date and time in the same room."""
        day, start_time, end_time = _parse_date(date), parse_time(start), parse_time(end)
        return manager.call(
            session_id,
            RESCHEDULE_BOOKING,
            {
                "session_id": session_id,
                "booking_id": booking_id,
                "date": date,
                "start": start,
                "end": end,
            },
            lambda world: {
                "booking": booking_payload(world.reschedule(booking_id, day, start_time, end_time))
            },
        )

    @mcp.tool
    def cancel_booking(session_id: SessionIdArg, booking_id: BookingIdArg) -> dict[str, Any]:
        """Cancel a booking."""
        return manager.call(
            session_id,
            "cancel_booking",
            {"session_id": session_id, "booking_id": booking_id},
            lambda world: {"booking": booking_payload(world.cancel(booking_id))},
        )

    @mcp.tool
    def finish_task(session_id: SessionIdArg, summary: SummaryArg) -> dict[str, Any]:
        """Finish the task and close the session. Call it once you are done."""
        return manager.finish(session_id, summary)

    return mcp


def _parse_date(value: str) -> date:
    """Parse a schema-validated ISO date."""
    return date.fromisoformat(value)


def experiment_client_from_settings(settings: Settings) -> ExperimentClient:
    """The Flagsmith client, or an offline control-style client when no key is configured."""
    if settings.flagsmith_environment_key is None:
        logger.warning(
            "FLAGSMITH_ENVIRONMENT_KEY is not set: every session gets the control style "
            "outside any experiment"
        )
        return NullExperimentClient()
    return FlagsmithExperimentClient.from_settings(settings)


def manager_from_settings(settings: Settings) -> SessionManager:
    """Build the session manager on the configured ledger and experiment client."""
    return SessionManager(
        Ledger(make_engine(settings)),
        experiment_client_from_settings(settings),
        max_tool_calls=settings.sa_max_tool_calls,
        idle_seconds=settings.sa_session_idle_seconds,
    )


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point: serve over streamable HTTP or stdio."""
    parser = argparse.ArgumentParser(description="Second Audience reference booking MCP server.")
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    server = build_server(manager_from_settings(settings), settings)
    # The banner also triggers FastMCP's update check, a request to PyPI on every start.
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(
            transport="http", host=args.host, port=args.port, path=MCP_PATH, show_banner=False
        )


if __name__ == "__main__":
    main()
