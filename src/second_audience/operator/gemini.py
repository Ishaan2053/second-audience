"""One Gemini call shape the operator uses: fixed instructions in, schema-constrained JSON out."""

from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

JsonModel = Callable[[str], str]
"""Sends one prompt and returns the model's raw JSON answer."""


def gemini_json(api_key: str, model: str, instructions: str, schema: dict[str, Any]) -> JsonModel:
    """A `JsonModel` on the Gemini API with its output constrained to `schema`."""
    # The SDK's own retry covers the 503 "high demand" answers seen in the lab (spike
    # findings, SA-08). Each operator command sends one or two requests, so there is no
    # quota pacing to protect.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=3)),
    )

    def ask(prompt: str) -> str:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=instructions,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return response.text or ""

    return ask
