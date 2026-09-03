"""The shape the model is asked to return.

This is written out rather than generated from the Pydantic model, for two reasons. The
allowlist of action names is per-run, so the schema tells the model exactly which
capabilities this supervisor has instead of offering five and refusing three. And the
runtime capabilities that belong to later work — a proposed memory refresh, generated
wake guidance — are deliberately absent, so the model is never invited to produce
something the workflow would only discard.

Whatever comes back is still validated by `DecisionProposal`. This schema reduces
malformed output; it does not decide what is acceptable.
"""

from typing import Any

from app.domain.actions import REGISTRY
from app.domain.vocabulary import ACTION_BATCH, MESSAGE_CHARS, SUBJECT_CHARS, NoteCategory

# Groq/OpenAI-style `json_schema` with strict validation, and Google's OpenAPI subset.
Dialect = str


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _actions(allowed: list[str]) -> dict[str, Any]:
    described = "; ".join(f"{name}: {REGISTRY[name].summary}" for name in allowed)
    return {
        "type": "array",
        "maxItems": ACTION_BATCH,
        "description": f"Business actions to record. Available here — {described}",
        "items": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": [str(name) for name in allowed]},
                "content": {
                    "type": "string",
                    "maxLength": MESSAGE_CHARS,
                    "description": "The message body or note text. Specific and self-contained.",
                },
                "subject": _nullable(
                    {
                        "type": "string",
                        "maxLength": SUBJECT_CHARS,
                        "description": "Required for a team message; omit for customer or note.",
                    }
                ),
                "category": _nullable(
                    {
                        "type": "string",
                        "enum": [str(item) for item in NoteCategory],
                        "description": "Required for create_internal_note only.",
                    }
                ),
                "issue_id": _nullable(
                    {
                        "type": "string",
                        "description": (
                            "The exact open_issue id this concerns. Required for every "
                            "message; optional for a note. Never invent one."
                        ),
                    }
                ),
                "rationale": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "One sentence naming the evidence that justifies this.",
                },
            },
            "required": ["action", "content", "subject", "category", "issue_id", "rationale"],
            "additionalProperties": False,
        },
    }


def proposal_schema(allowed: list[str], *, minimum: int, maximum: int) -> dict[str, Any]:
    """The canonical strict schema, in the OpenAI `json_schema` dialect."""
    return {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "maxLength": MESSAGE_CHARS,
                "description": (
                    "A short operational explanation of this decision and the evidence "
                    "behind it. Not private reasoning; this is shown in the run timeline."
                ),
            },
            "actions": _actions(allowed),
            "sleep_for_seconds": {
                "type": "integer",
                "minimum": minimum,
                "maximum": maximum,
                "description": (
                    f"When to review this order again, between {minimum} and {maximum} "
                    "seconds from now."
                ),
            },
            "completion_recommendation": _nullable(
                {
                    "type": "string",
                    "maxLength": 500,
                    "description": (
                        "Optional. A recommendation only; the workflow decides when a run "
                        "ends, and this never closes it."
                    ),
                }
            ),
        },
        "required": ["rationale", "actions", "sleep_for_seconds", "completion_recommendation"],
        "additionalProperties": False,
    }


def to_openapi(node: Any) -> Any:
    """Rewrite the strict schema into Google's OpenAPI 3.0 subset.

    That dialect has no `additionalProperties` and expresses an optional value as
    `nullable` rather than a union with null, so an unconverted schema is rejected
    outright with a 400.
    """
    if isinstance(node, list):
        return [to_openapi(item) for item in node]
    if not isinstance(node, dict):
        return node

    variants = node.get("anyOf")
    if variants and any(item.get("type") == "null" for item in variants):
        concrete = [item for item in variants if item.get("type") != "null"]
        if len(concrete) == 1:
            return {**to_openapi(concrete[0]), "nullable": True}

    converted = {
        key: to_openapi(value) for key, value in node.items() if key != "additionalProperties"
    }
    if "properties" in converted:
        # Keep the order stable so the model fills the sections in a predictable order.
        converted["propertyOrdering"] = list(converted["properties"])
    return converted
