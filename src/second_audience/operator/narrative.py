"""Prose for the report's Summary and Decision sections, drafted by a model or a template.

The model only writes words around numbers computed in code. It gets the numbers as JSON and
must copy them. Every number in its draft is then checked against that JSON; one number that
is not there, a malformed answer, an API error or no key at all, and the deterministic
template is used instead.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from second_audience.operator.analysis import Analysis, Decision

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

NARRATIVE_INSTRUCTIONS = (
    "You write two short sections of an A/B test report for engineers. The test compares two "
    "styles of error message that an AI agent receives from a room-booking tool server. "
    "Use only the facts in the JSON you are given. Copy every number exactly as it is written "
    "there, and write no other number, date, percentage or count. Do not round, convert or "
    "combine numbers. Do not name a winner unless the decision is 'ship'. Plain sentences, no "
    "lists, no Markdown headings. 'summary': three or four sentences stating the question, "
    "what was measured, and the decision. 'decision': two or three sentences explaining how "
    "the pre-registered rule produced the decision."
)


class Narrative(BaseModel):
    """The two prose sections."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    decision: str


Drafter = Callable[[dict[str, Any]], str]
"""Sends the facts to a model and returns its raw JSON answer."""


@dataclass(frozen=True)
class Written:
    """The narrative used in the report and who wrote it."""

    narrative: Narrative
    author: str


def facts(analysis: Analysis) -> dict[str, Any]:
    """The numbers the model may use, as display strings so it can copy them verbatim."""
    spec, rule = analysis.spec, analysis.spec.stopping_rule
    primary = analysis.rows[0]
    facts: dict[str, Any] = {
        "experiment": spec.slug,
        "question": spec.hypothesis,
        "arms": {"control": "informative error", spec.variants[0].key: "prescriptive error"},
        "primary_metric": primary.metric.name,
        "primary_direction": "lower is better"
        if primary.metric.direction == "down"
        else "higher is better",
        "exposed_sessions_per_arm": {k: str(v) for k, v in analysis.exposures.items()},
        "min_exposed_per_arm": str(rule.min_exposed_per_arm),
        "hard_end": rule.hard_end.date().isoformat(),
        "stopping_rule_met": analysis.decision.rule_met,
        "decision": analysis.decision.decision.value,
        "rule_steps": analysis.decision.reasons,
        "win_probability_threshold": f"{rule.win_probability_threshold:g}",
    }
    for row in analysis.rows:
        entry: dict[str, Any] = {
            "per_arm": {
                arm: (f"{s.mean:.2f}" if s is not None and s.mean is not None else None)
                for arm, s in row.stats.items()
            }
        }
        # Inference is withheld until the stopping rule is met, as in the report itself.
        if analysis.decision.rule_met and row.inference is not None:
            entry["chance_to_win"] = f"{row.inference.chance_to_win:.3f}"
            entry["lift_interval"] = [f"{row.inference.ci_low:.3f}", f"{row.inference.ci_high:.3f}"]
        facts[row.metric.name] = entry
    return facts


def _normalise(number: str) -> str:
    """`4.30`, `4.3` and `04.3` compare equal."""
    try:
        return format(Decimal(number).normalize(), "f")
    except InvalidOperation:
        return number


def unsupported_numbers(text: str, source: dict[str, Any]) -> list[str]:
    """Numbers in `text` that do not appear anywhere in `source`."""
    allowed = {_normalise(n) for n in _NUMBER.findall(json.dumps(source))}
    return [n for n in _NUMBER.findall(text) if _normalise(n) not in allowed]


def template(analysis: Analysis) -> Narrative:
    """The deterministic fallback, built from the same numbers."""
    spec, decision = analysis.spec, analysis.decision
    rule = spec.stopping_rule
    primary = analysis.rows[0].metric.name
    counts = ", ".join(f"{arm} {n}" for arm, n in analysis.exposures.items())
    summary = (
        f"Experiment {spec.slug} asks whether an error that suggests an alternative and a "
        f"corrected call (prescriptive) lowers {primary.lower()} compared with an error that "
        f"only states the fact (informative). Flagsmith counts {counts} exposed sessions. "
    )
    if not decision.rule_met:
        summary += (
            f"The stopping rule needs {rule.min_exposed_per_arm} exposed sessions per arm or "
            "the hard end, so this is an interim report and names no winner."
        )
    else:
        summary += f"Under the pre-registered rule the decision is: {decision.decision.value}."
    explanation = {
        Decision.SHIP: "The variant met the win-probability threshold and no guardrail was "
        "credibly worse, so the prescriptive style is shipped.",
        Decision.NO_SHIP: "The variant did not meet every condition of the rule, so the "
        "informative style is kept.",
        Decision.INCONCLUSIVE: "The rule could not be applied yet, so no change is made.",
    }[decision.decision]
    return Narrative(summary=summary, decision=explanation)


def write_narrative(analysis: Analysis, drafter: Drafter | None, model: str) -> Written:
    """A checked model draft, or the template with the reason it was used."""
    if drafter is None:
        return Written(template(analysis), "fixed template (no model used)")
    source = facts(analysis)
    try:
        draft = Narrative.model_validate_json(drafter(source))
    except ValidationError as error:
        logger.warning("narrative draft was malformed: %s", error)
        return Written(template(analysis), "fixed template (the model's draft was malformed)")
    except Exception as error:
        logger.warning("narrative model call failed: %s", error)
        reason = f"{type(error).__name__} {getattr(error, 'code', '')}".strip()
        return Written(template(analysis), f"fixed template (the model call failed: {reason})")
    bad = unsupported_numbers(f"{draft.summary} {draft.decision}", source)
    if bad:
        logger.warning("narrative draft rejected; numbers not in its input: %s", bad)
        return Written(
            template(analysis),
            f"fixed template (the model's draft used numbers not in its input: {', '.join(bad)})",
        )
    return Written(draft, f"drafted by {model} from the computed numbers; every number checked")


def gemini_drafter(api_key: str, model: str) -> Drafter:
    """A `Drafter` on the Gemini API with JSON output constrained to `Narrative`."""
    # The SDK's own retry covers the 503 "high demand" answers seen in the lab (spike
    # findings, SA-08). One report is one request, so there is no quota pacing to protect.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=3)),
    )

    def draft(source: dict[str, Any]) -> str:
        response = client.models.generate_content(
            model=model,
            contents=json.dumps(source, indent=2),
            config=types.GenerateContentConfig(
                system_instruction=NARRATIVE_INSTRUCTIONS,
                response_mime_type="application/json",
                response_json_schema=Narrative.model_json_schema(),
            ),
        )
        return response.text or ""

    return draft
