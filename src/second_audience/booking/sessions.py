"""Session lifecycle: one sandboxed world per agent session, with a budget, exposure and scoring.

Every domain tool call goes through `SessionManager.call`: validate the session, count the
call, run the world operation, render any domain error in the session's style (resolving the
style, and so recording the exposure, at the first styled error), and record the call in the
ledger. Finalisation scores the final state once and sends the experiment event, if any.
"""

import hmac
import logging
import random
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from second_audience.booking.checker import Outcome, check
from second_audience.booking.errors import STYLED_CODES, error_payload, render_error
from second_audience.booking.scenarios import SCENARIOS, Scenario, get_scenario
from second_audience.booking.suggestions import AttemptedCall
from second_audience.booking.world import DomainError, World
from second_audience.core.experiment import (
    TASK_COMPLETED,
    TASK_FINISHED,
    TASK_WRONG_BOOKING,
    ExperimentClient,
)
from second_audience.core.ledger import Ledger, Source
from second_audience.core.styles import CONTROL, ErrorStyle

logger = logging.getLogger(__name__)

SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
INVALID_ARGUMENTS = "INVALID_ARGUMENTS"

FINISH_TASK = "finish_task"

LAB_KEY_HEADER = "x-sa-lab-key"
AGENT_LABEL_HEADER = "x-sa-agent-label"
UNKNOWN_AGENT = "unknown"
MAX_AGENT_LABEL_LENGTH = 200

SANDBOX_NOTE = (
    "This is a research sandbox. The rooms, people and bookings are fictional, and every "
    "tool call in this session is logged. Call finish_task when you are done."
)

# The experiment event each scored final state sends, for exposed sessions in the experiment.
_EVENTS: dict[Outcome, str] = {
    Outcome.CORRECT: TASK_COMPLETED,
    Outcome.INCORRECT: TASK_WRONG_BOOKING,
}

Operation = Callable[[World], dict[str, Any]]
"""A world operation behind a domain tool: returns the success payload or raises."""


@dataclass(frozen=True, slots=True)
class Identity:
    """Where a session's traffic comes from, and the agent label stored with it."""

    source: Source
    agent_label: str


def identify(headers: Mapping[str, str], client_name: str | None, lab_key: str | None) -> Identity:
    """Lab if the request carries the lab key; otherwise field, labelled by the MCP client name.

    `headers` must have lower-case names, as FastMCP's `get_http_headers` returns them.
    """
    presented = headers.get(LAB_KEY_HEADER)
    if lab_key and presented and hmac.compare_digest(presented.encode(), lab_key.encode()):
        label = headers.get(AGENT_LABEL_HEADER) or UNKNOWN_AGENT
        return Identity("lab", label[:MAX_AGENT_LABEL_LENGTH])
    return Identity("field", (client_name or UNKNOWN_AGENT)[:MAX_AGENT_LABEL_LENGTH])


@dataclass(eq=False)
class Session:
    """One agent session's in-memory state. Guarded by its own lock."""

    session_id: str
    scenario: Scenario
    world: World
    last_active: float
    traits: dict[str, str]  # sent to Flagsmith with the exposure
    calls: int = 0
    style: ErrorStyle | None = None  # set at the first styled error, which is the exposure
    in_experiment: bool = False
    outcome: Outcome | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def _not_found(session_id: str) -> dict[str, Any]:
    """Payload for a call naming a session that is unknown, finished or expired."""
    return error_payload(
        SESSION_NOT_FOUND, f"No open session has ID {session_id}. Call begin_task to start one."
    )


def _budget_exhausted(limit: int) -> dict[str, Any]:
    """Payload for a call made after the session's tool-call budget ran out."""
    return error_payload(
        BUDGET_EXHAUSTED, f"This session has used all {limit} tool calls and has been closed."
    )


class SessionManager:
    """Creates, looks up, budgets, finalises and expires sessions. Safe across threads."""

    def __init__(
        self,
        ledger: Ledger,
        experiment: ExperimentClient,
        *,
        max_tool_calls: int,
        idle_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind to the ledger and experiment client; `clock` is injectable for tests."""
        self._ledger = ledger
        self._experiment = experiment
        self._max_tool_calls = max_tool_calls
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def begin(self, scenario_id: str | None, identity: Identity) -> dict[str, Any]:
        """Start a session on the given scenario (random if omitted) and return its brief."""
        scenario = get_scenario(scenario_id) if scenario_id else random.choice(SCENARIOS)
        session_id = uuid.uuid4().hex
        self._ledger.start_session(
            session_id,
            scenario.id,
            scenario.kind,
            identity.source,
            identity.agent_label,
            client_ip_hash=None,
        )
        traits = {
            "agent_label": identity.agent_label,
            "source": identity.source,
            "scenario_kind": scenario.kind,
        }
        session = Session(session_id, scenario, scenario.new_world(), self._clock(), traits)
        with self._lock:
            self._sessions[session_id] = session
        return {
            "session_id": session_id,
            "brief": scenario.brief,
            "max_tool_calls": self._max_tool_calls,
            "note": SANDBOX_NOTE,
        }

    def call(
        self, session_id: str, tool: str, arguments: Mapping[str, Any], operation: Operation
    ) -> dict[str, Any]:
        """Run one budgeted domain tool call and record it; returns the response payload."""
        session = self._get(session_id)
        if session is None:
            return _not_found(session_id)
        with session.lock:
            session.last_active = self._clock()
            if session.outcome is Outcome.BUDGET_EXHAUSTED:
                return _budget_exhausted(self._max_tool_calls)
            if session.outcome is not None:
                return _not_found(session_id)
            if session.calls >= self._max_tool_calls:
                self._finalise(session, Outcome.BUDGET_EXHAUSTED)
                return _budget_exhausted(self._max_tool_calls)
            session.calls += 1
            payload, error_code, styled = self._run(session, tool, arguments, operation)
            self._ledger.record_tool_call(
                session_id, session.calls, tool, arguments, payload, error_code, styled
            )
            return payload

    def finish(self, session_id: str, summary: str) -> dict[str, Any]:
        """Finalise and score the session. Idempotent: later calls return the same outcome."""
        session = self._get(session_id)
        if session is None:
            return _not_found(session_id)
        with session.lock:
            session.last_active = self._clock()
            already_finished = session.outcome is not None
            outcome = self._finalise(session, None)
            payload = {"session_id": session_id, "finished": True, "outcome": outcome.value}
            if not already_finished:
                # finish_task does not count against the budget, but its summary belongs in
                # the transcript, after the last domain call.
                self._ledger.record_tool_call(
                    session_id,
                    session.calls + 1,
                    FINISH_TASK,
                    {"session_id": session_id, "summary": summary},
                    payload,
                    None,
                    False,
                )
            return payload

    def expire_idle(self) -> int:
        """Finalise idle open sessions as abandoned and forget all idle ones. Returns the count."""
        cutoff = self._clock() - self._idle_seconds
        with self._lock:
            idle = [s for s in self._sessions.values() if s.last_active <= cutoff]
        expired = 0
        for session in idle:
            with session.lock:
                if session.last_active > cutoff:
                    continue  # a call arrived since the scan
                self._finalise(session, Outcome.ABANDONED)
                with self._lock:
                    self._sessions.pop(session.session_id, None)
                expired += 1
        return expired

    def close(self) -> None:
        """Finalise every open session as abandoned (a server stop) and flush events."""
        with self._lock:
            remaining = list(self._sessions.values())
            self._sessions.clear()
        for session in remaining:
            with session.lock:
                self._finalise(session, Outcome.ABANDONED)
        self._experiment.flush()

    def _get(self, session_id: str) -> Session | None:
        """Look up an in-memory session."""
        with self._lock:
            return self._sessions.get(session_id)

    def _run(
        self, session: Session, tool: str, arguments: Mapping[str, Any], operation: Operation
    ) -> tuple[dict[str, Any], str | None, bool]:
        """Run the operation; returns the payload, the error code and whether it was styled."""
        try:
            return operation(session.world), None, False
        except DomainError as error:
            style = self._style_for(session, error)
            rendered = render_error(error, session.world, style, AttemptedCall(tool, arguments))
            return rendered.payload, error.code, rendered.styled
        except ValueError as error:
            # Input the schema cannot express, such as a start time after the end time.
            return error_payload(INVALID_ARGUMENTS, str(error)), INVALID_ARGUMENTS, False

    def _style_for(self, session: Session, error: DomainError) -> ErrorStyle:
        """The session's style; the first styled error resolves it and records the exposure."""
        if error.code not in STYLED_CODES:
            return session.style or CONTROL  # unstyled errors render the same in every style
        if session.style is None:
            style, variant = self._experiment.style_for(session.session_id, session.traits)
            session.style = style
            session.in_experiment = variant is not None
            self._ledger.record_exposure(
                session.session_id, variant, in_experiment=session.in_experiment
            )
        return session.style

    def _finalise(self, session: Session, reason: Outcome | None) -> Outcome:
        """Score and store the outcome once; send the event for exposed experiment sessions.

        `reason` overrides the stored outcome (budget exhaustion, abandonment). The final state
        is always scored and stored, and the event follows it, except for abandoned sessions.
        """
        if session.outcome is not None:
            return session.outcome
        final_state = check(session.world, session.scenario)
        outcome = reason or final_state
        self._ledger.finish_session(
            session.session_id, outcome, session.calls, final_state=final_state
        )
        session.outcome = outcome
        if session.in_experiment and outcome is not Outcome.ABANDONED:
            event = _EVENTS.get(final_state)
            if event:
                self._experiment.track(session.session_id, event)
            # Efficiency metric (docs/08-flagsmith-addons.md, A2): domain calls used.
            self._experiment.track(session.session_id, TASK_FINISHED, value=session.calls)
            self._experiment.flush()
        logger.info(
            "session %s finished: outcome=%s final_state=%s calls=%d",
            session.session_id,
            outcome,
            final_state,
            session.calls,
        )
        return outcome
