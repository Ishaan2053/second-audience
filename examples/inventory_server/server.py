"""A tiny stock-reservation MCP server whose "insufficient stock" error is under test.

It shows `second_audience.core.SecondAudience` on a domain unrelated to room booking. One
error, `INSUFFICIENT_STOCK`, is styled by the session's `ErrorStyle`: the informative style
states the fact; the prescriptive style adds a warehouse that has enough stock and a corrected
call. The style is resolved, and the exposure recorded, at the session's first such error.

Run it with `uv run python -m examples.inventory_server.server` (stdio). Without
`FLAGSMITH_ENVIRONMENT_KEY` every session gets the control style; with it, the style comes
from the flag named by `SA_EXAMPLE_FLAG_NAME`.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from second_audience.config import Settings, get_settings
from second_audience.core import TASK_COMPLETED, TASK_FINISHED, ErrorStyle, SecondAudience

SERVER_NAME = "second-audience-inventory-example"
INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
RESERVE_STOCK = "reserve_stock"
CAUTION_NOTE = "Check the suggestion against the customer's requirements before using it."

Sku = Literal["SKU-100", "SKU-200"]
Warehouse = Literal["EAST", "WEST"]

ITEMS: dict[str, str] = {"SKU-100": "USB-C cable", "SKU-200": "Laptop stand"}
OPENING_STOCK: dict[tuple[str, str], int] = {
    ("SKU-100", "EAST"): 3,
    ("SKU-100", "WEST"): 12,
    ("SKU-200", "EAST"): 20,
    ("SKU-200", "WEST"): 0,
}
TASK = "Reserve 5 USB-C cables (SKU-100) for the customer, then call finish_order."

SessionIdArg = Annotated[str, Field(min_length=1, max_length=64, description="From start_order.")]
QuantityArg = Annotated[int, Field(ge=1, le=100, description="Units to reserve.")]
SummaryArg = Annotated[str, Field(max_length=2000, description="What you reserved.")]


@dataclass(eq=False)
class Order:
    """One session's sandboxed stock and the reservations made in it."""

    stock: dict[tuple[str, str], int] = field(default_factory=lambda: dict(OPENING_STOCK))
    reservations: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def error_payload(code: str, message: str) -> dict[str, Any]:
    """The error payload every style shares: a stable code and a plain message."""
    return {"error": {"code": code, "message": message}}


def insufficient_stock(
    order: Order, sku: str, warehouse: str, quantity: int, style: ErrorStyle
) -> dict[str, Any]:
    """Render the insufficient-stock error in `style`; suggestion fields need an alternative."""
    available = order.stock[(sku, warehouse)]
    payload = error_payload(
        INSUFFICIENT_STOCK,
        f"Warehouse {warehouse} has {available} of {sku}; {quantity} requested.",
    )
    alternative = next(
        (w for (s, w), units in order.stock.items() if s == sku and units >= quantity), None
    )
    if not style.suggest_alternative or alternative is None:
        return payload
    payload["suggestion"] = {"warehouse": alternative, "available": order.stock[(sku, alternative)]}
    if style.corrected_call:
        payload["corrected_call"] = {
            "tool": RESERVE_STOCK,
            "arguments": {"sku": sku, "warehouse": alternative, "quantity": quantity},
        }
    if style.caution_note:
        payload["caution_note"] = CAUTION_NOTE
    return payload


def build_server(sa: SecondAudience) -> FastMCP:
    """Create the FastMCP app with three stock tools, styled through `sa`."""
    orders: dict[str, Order] = {}
    lock = threading.Lock()

    def _order(session_id: str) -> Order | None:
        with lock:
            return orders.get(session_id)

    mcp = FastMCP(SERVER_NAME, strict_input_validation=True, mask_error_details=True)

    @mcp.tool
    def start_order() -> dict[str, Any]:
        """Start a reservation task. Returns a session_id, the task and the current stock."""
        session_id = uuid.uuid4().hex
        order = Order()
        with lock:
            orders[session_id] = order
        stock = [
            {"sku": sku, "item": ITEMS[sku], "warehouse": warehouse, "available": units}
            for (sku, warehouse), units in order.stock.items()
        ]
        return {"session_id": session_id, "task": TASK, "stock": stock}

    @mcp.tool
    def reserve_stock(
        session_id: SessionIdArg, sku: Sku, warehouse: Warehouse, quantity: QuantityArg
    ) -> dict[str, Any]:
        """Reserve units of one SKU from one warehouse."""
        order = _order(session_id)
        if order is None:
            return error_payload(SESSION_NOT_FOUND, f"No open session has ID {session_id}.")
        with order.lock:
            order.calls += 1
            if order.stock[(sku, warehouse)] < quantity:
                style = sa.variant(session_id)  # lazy exposure: the first styled error
                return insufficient_stock(order, sku, warehouse, quantity, style)
            order.stock[(sku, warehouse)] -= quantity
            reservation = {"sku": sku, "warehouse": warehouse, "quantity": quantity}
            order.reservations.append(reservation)
            return {"reservation": reservation}

    @mcp.tool
    def finish_order(session_id: SessionIdArg, summary: SummaryArg) -> dict[str, Any]:
        """Finish the task and close the session."""
        with lock:
            order = orders.pop(session_id, None)
        if order is None:
            return error_payload(SESSION_NOT_FOUND, f"No open session has ID {session_id}.")
        with order.lock:
            reserved = sum(r["quantity"] for r in order.reservations if r["sku"] == "SKU-100")
            calls = order.calls
        completed = reserved == 5
        if completed:
            sa.outcome(session_id, TASK_COMPLETED)
        sa.outcome(session_id, TASK_FINISHED, value=calls)
        sa.end(session_id)
        return {"session_id": session_id, "finished": True, "completed": completed}

    return mcp


def second_audience_from_settings(settings: Settings) -> SecondAudience:
    """The example's `SecondAudience`, on its own flag, never the running experiment's flag."""
    if settings.sa_example_flag_name == settings.sa_flag_name:
        raise ValueError(
            "SA_EXAMPLE_FLAG_NAME must differ from SA_FLAG_NAME: the example must not take "
            "part in the reference experiment"
        )
    return SecondAudience.from_settings(settings, flag_name=settings.sa_example_flag_name)


def main() -> None:
    """Serve the example over stdio."""
    logging.basicConfig(level=logging.INFO)
    sa = second_audience_from_settings(get_settings())
    try:
        build_server(sa).run(transport="stdio", show_banner=False)
    finally:
        sa.flush()


if __name__ == "__main__":
    main()
