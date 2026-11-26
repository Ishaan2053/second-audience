"""Gemini conversion helpers, error classification, and one real session per configured model."""

from typing import Any

import pytest
from google.genai import errors, types

from second_audience.lab.models import (
    ModelTurn,
    QuotaExhaustedError,
    RetryLaterError,
    ToolCall,
    ToolResult,
    UserMessage,
    _to_contents,
    _to_turn,
    classify_api_error,
    strip_keywords,
)


def _quota_error(quota_id: str, retry_delay: str | None = None) -> errors.APIError:
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id, "quotaValue": "20"}],
        }
    ]
    if retry_delay:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "quota",
            "details": details,
        }
    }
    return errors.ClientError(429, body)


def test_per_day_quota_is_exhausted() -> None:
    error = classify_api_error(_quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert isinstance(error, QuotaExhaustedError)


def test_per_minute_quota_is_rate_limited_with_delay() -> None:
    error = classify_api_error(
        _quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "14s")
    )
    assert isinstance(error, RetryLaterError)
    assert error.retry_after == 14.0


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_server_errors_are_retried_later(code: int) -> None:
    error = classify_api_error(errors.ServerError(code, {"error": {"status": "UNAVAILABLE"}}))
    assert isinstance(error, RetryLaterError)
    assert error.retry_after is None


def test_other_errors_are_not_classified() -> None:
    assert classify_api_error(errors.ClientError(400, {"error": {"message": "bad"}})) is None


def test_strip_keywords_keeps_property_names() -> None:
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "title": "Title"}},
        "title": "args",
    }
    clean, stripped = strip_keywords(schema, frozenset({"title"}))
    assert clean == {"type": "object", "properties": {"title": {"type": "string"}}}
    assert stripped == ["$.properties.title.title", "$.title"]


def test_parallel_tool_results_share_one_content() -> None:
    a, b = ToolCall("list_rooms", {"session_id": "x"}, "1"), ToolCall("finish_task", {}, "2")
    contents = _to_contents(
        [
            UserMessage("brief"),
            ModelTurn(text="", tool_calls=[a, b]),
            ToolResult(a, {"rooms": []}),
            ToolResult(b, {"finished": True}),
        ]
    )
    assert [c.role for c in contents] == ["user", "model", "user"]
    responses = [p.function_response for p in contents[2].parts or []]
    assert [(r.id, r.name) for r in responses if r] == [("1", "list_rooms"), ("2", "finish_task")]


def test_model_content_is_replayed_unchanged() -> None:
    content = types.Content(
        role="model",
        parts=[
            types.Part(text="thinking", thought=True),
            types.Part(
                function_call=types.FunctionCall(id="1", name="list_rooms", args={"a": 1}),
                thought_signature=b"sig",
            ),
        ],
    )
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=content)], model_version="v1"
    )
    turn = _to_turn(response)
    assert turn.text == ""  # thoughts are not text
    assert turn.tool_calls == [ToolCall("list_rooms", {"a": 1}, "1")]
    assert turn.model_version == "v1"
    assert _to_contents([turn]) == [content]


def test_empty_response_is_an_empty_turn() -> None:
    turn = _to_turn(types.GenerateContentResponse(candidates=[]))
    assert (turn.text, turn.tool_calls, turn.provider_content) == ("", [], None)
