"""Model clients for the lab: a provider-neutral protocol and the Gemini implementation.

The runner speaks only in the types defined here. Provider SDK types stay inside each client;
a client may stash its own content on a `ModelTurn` (`provider_content`) to replay it
unchanged, which Gemini needs for thought signatures on function calls.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from google import genai
from google.genai import errors, types

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an assistant with access to room-booking tools. Complete the user's request "
    "using the tools. Follow the user's requirements exactly. When you have finished, call "
    "finish_task with a one-sentence summary."
)
"""Fixed for every lab session; quoted in HYPOTHESIS.md. Do not tune it."""

GEMINI_REQUEST_TIMEOUT_MS = 120_000

GEMINI_UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset()
"""JSON Schema keywords Gemini rejects in `parameters_json_schema`; stripped before sending.

Empty: the live check in SA-08 accepted every keyword the booking server's tools use (see
docs/spike-findings.md). A keyword added here weakens schema strictness for Gemini only.
"""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool offered to the model: name, description and JSON Schema for its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One function call the model asked for."""

    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class UserMessage:
    """A user message (the task brief)."""

    text: str


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """One model response: its text and the tool calls it requested."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model_version: str | None = None
    provider_content: object = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The result of executing one tool call, fed back to the model."""

    call: ToolCall
    result: dict[str, Any]


Message = UserMessage | ModelTurn | ToolResult


class RetryLaterError(Exception):
    """A per-minute limit or a temporary overload; the same request can be retried later."""

    def __init__(self, retry_after: float | None) -> None:
        """Store the provider's suggested delay in seconds, if it gave one."""
        hint = f"retry after {retry_after:.0f}s" if retry_after is not None else "no delay given"
        super().__init__(f"retry later ({hint})")
        self.retry_after = retry_after


class QuotaExhaustedError(Exception):
    """The model's daily quota is used up until the provider's next reset."""


class ModelClient(Protocol):
    """One model behind a provider SDK. `step` sends the conversation and returns one turn."""

    name: str
    sdk_version: str

    async def step(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        """Send the conversation so far with the tools; return the model's next turn."""
        ...


def strip_keywords(schema: Any, keywords: frozenset[str], path: str = "$") -> tuple[Any, list[str]]:
    """Remove `keywords` from a JSON Schema at every depth; return it and the stripped paths."""
    if isinstance(schema, list):
        stripped: list[str] = []
        items = []
        for index, item in enumerate(schema):
            clean, removed = strip_keywords(item, keywords, f"{path}[{index}]")
            items.append(clean)
            stripped.extend(removed)
        return items, stripped
    if not isinstance(schema, dict):
        return schema, []
    result: dict[str, Any] = {}
    stripped = []
    for key, value in schema.items():
        # Keys under `properties` are argument names, not keywords.
        if key in keywords and not path.endswith(".properties"):
            stripped.append(f"{path}.{key}")
            continue
        result[key], removed = strip_keywords(value, keywords, f"{path}.{key}")
        stripped.extend(removed)
    return result, stripped


def classify_api_error(error: errors.APIError) -> Exception | None:
    """Map a Gemini 429 to `QuotaExhaustedError` (per-day quota) or `RetryLaterError`.

    A 503 ("model is currently experiencing high demand", seen in the SA-08 pilot) is
    transient too, so it is retried like a per-minute limit instead of failing the run.
    """
    if error.code == 503:
        return RetryLaterError(None)
    if error.code != 429:
        return None
    body = error.details.get("error", error.details) if isinstance(error.details, dict) else {}
    details = body.get("details", []) if isinstance(body, dict) else []
    retry_after: float | None = None
    for detail in details:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations", []):
            if "PerDay" in str(violation.get("quotaId", "")):
                return QuotaExhaustedError(error.message or "daily quota exhausted")
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                retry_after = float(delay[:-1])
            except ValueError:
                retry_after = None
    return RetryLaterError(retry_after)


class GeminiModelClient:
    """A Gemini model through the Google Gen AI SDK, at default temperature, SDK retries off."""

    def __init__(self, name: str, api_key: str) -> None:
        """Create the SDK client for model `name`."""
        self.name = name
        self.sdk_version = f"google-genai {genai.__version__}"
        # No retry_options: the SDK then sends each request once, so the runner's pacing
        # and backoff decide every request that counts against the free-tier quota.
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS)
        )
        self._declarations: dict[tuple[str, ...], list[types.FunctionDeclaration]] = {}

    async def step(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        """Send one generate_content request and convert the response."""
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=self._function_declarations(tools))],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.name, contents=_to_contents(messages), config=config
            )
        except errors.APIError as error:
            mapped = classify_api_error(error)
            if mapped is not None:
                raise mapped from error
            raise
        return _to_turn(response)

    def _function_declarations(self, tools: Sequence[ToolSpec]) -> list[types.FunctionDeclaration]:
        """Convert tool specs once per tool set, logging any stripped schema keyword."""
        key = tuple(tool.name for tool in tools)
        if key not in self._declarations:
            declarations = []
            for tool in tools:
                schema, stripped = strip_keywords(tool.parameters, GEMINI_UNSUPPORTED_KEYWORDS)
                if stripped:
                    logger.warning("%s: stripped from %s: %s", self.name, tool.name, stripped)
                declarations.append(
                    types.FunctionDeclaration(
                        name=tool.name,
                        description=tool.description,
                        parameters_json_schema=schema,
                    )
                )
            self._declarations[key] = declarations
        return self._declarations[key]


def _to_contents(messages: Sequence[Message]) -> list[types.Content]:
    """Convert the conversation; consecutive tool results share one content, as Gemini expects."""
    contents: list[types.Content] = []
    for message in messages:
        if isinstance(message, ToolResult):
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message.call.call_id, name=message.call.name, response=message.result
                )
            )
            previous = contents[-1] if contents else None
            if previous is not None and previous.parts and previous.parts[0].function_response:
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
        else:
            contents.append(_to_content(message))
    return contents


def _to_content(message: UserMessage | ModelTurn) -> types.Content:
    """Convert a user message or a model turn to Gemini content."""
    if isinstance(message, UserMessage):
        return types.Content(role="user", parts=[types.Part(text=message.text)])
    if isinstance(message.provider_content, types.Content):
        return message.provider_content  # replayed as-is, keeping thought signatures
    parts = [types.Part(text=message.text)] if message.text else []
    parts += [
        types.Part(
            function_call=types.FunctionCall(id=call.call_id, name=call.name, args=call.arguments)
        )
        for call in message.tool_calls
    ]
    return types.Content(role="model", parts=parts)


def _to_turn(response: types.GenerateContentResponse) -> ModelTurn:
    """Convert a Gemini response to a neutral turn. No candidate means an empty turn."""
    content = response.candidates[0].content if response.candidates else None
    parts = (content.parts if content else None) or []
    text = "".join(part.text for part in parts if part.text and not part.thought)
    calls = [
        ToolCall(
            name=part.function_call.name or "",
            arguments=dict(part.function_call.args or {}),
            call_id=part.function_call.id,
        )
        for part in parts
        if part.function_call is not None
    ]
    return ModelTurn(
        text=text,
        tool_calls=calls,
        model_version=response.model_version,
        provider_content=content if parts else None,
    )
