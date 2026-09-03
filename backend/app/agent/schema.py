"""The shape the model is asked to return.

This is written out rather than generated from the Pydantic model, because what the model
is asked for varies per run and per decision. The allowlist of action names is per-run, so
the schema names exactly the capabilities this supervisor has instead of offering five and
refusing three. A memory refresh is offered only when one is actually due, and its cutoff
is capped at the evidence this decision received. Anything the workflow would only discard
is left out, so the model is never invited to produce it.

Whatever comes back is still validated by `DecisionProposal`. This schema reduces
malformed output; it does not decide what is acceptable.
"""

from typing import Any

from app.contracts.report import NARRATIVE_ITEMS
from app.domain.actions import REGISTRY
from app.domain.vocabulary import (
    ACTION_BATCH,
    GUIDANCE_HINTS,
    MESSAGE_CHARS,
    SUBJECT_CHARS,
    SUMMARY_CHARS,
    KnownEvent,
    NoteCategory,
)

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


def _memory(cutoff: int) -> dict[str, Any]:
    return _nullable(
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "maxLength": SUMMARY_CHARS,
                    "description": (
                        "A replacement running summary of this order. State what happened "
                        "and what is still open. Do not claim an action was taken unless it "
                        "appears under already recorded actions."
                    ),
                },
                "through_sequence": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": cutoff,
                    "description": (
                        f"How far this summary covers. You were given evidence up to entry "
                        f"{cutoff}; claiming more is rejected."
                    ),
                },
            },
            "required": ["text", "through_sequence"],
            "additionalProperties": False,
        }
    )


def _guidance(issue_ids: list[str], *, minimum: int, maximum: int) -> dict[str, Any]:
    """The finite hint vocabulary, offered only when there is a concern to hang one on."""
    return _nullable(
        {
            "type": "array",
            "maxItems": GUIDANCE_HINTS,
            "description": (
                "Optional. How the cheap classifier should treat future events. A hint can "
                "bring a review forward or let routine progress pass quietly. It cannot "
                "grant permission, silence delivery or a delay, or override an operator."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["watch_for_progress", "shorten_review", "defer_routine"],
                        "description": (
                            "watch_for_progress: wake me when this event arrives while the "
                            "issue is open. shorten_review: look again sooner while it is "
                            "open. defer_routine: record this progress without waking me "
                            "when nothing is unresolved."
                        ),
                    },
                    "issue_id": _nullable(
                        {
                            "type": "string",
                            # Deliberately not an enum. A strict validator rejects the
                            # whole answer when a generated value falls outside one, and
                            # an unknown concern is a hint to refuse, not a decision to
                            # throw away. The gate names the valid ids; it also enforces
                            # them.
                            "description": (
                                "One of the open concern ids listed in the context. "
                                "Required for watch_for_progress and shorten_review; "
                                f"omit for defer_routine. Currently open: "
                                f"{', '.join(issue_ids)}."
                            ),
                        }
                    ),
                    "event_type": _nullable(
                        {
                            "type": "string",
                            "enum": [str(item) for item in KnownEvent],
                            "description": (
                                "Required for watch_for_progress and defer_routine. Only "
                                "ordinary progress may be deferred."
                            ),
                        }
                    ),
                    "review_after_seconds": _nullable(
                        {
                            "type": "integer",
                            "minimum": minimum,
                            "maximum": maximum,
                            "description": "Required for shorten_review only.",
                        }
                    ),
                    "expires_at": {
                        "type": "string",
                        "description": (
                            "When this hint stops applying, as an ISO 8601 UTC timestamp. "
                            "It cannot outlast the order's maximum age."
                        ),
                    },
                },
                "required": [
                    "kind",
                    "issue_id",
                    "event_type",
                    "review_after_seconds",
                    "expires_at",
                ],
                "additionalProperties": False,
            },
        }
    )


def proposal_schema(
    allowed: list[str],
    *,
    minimum: int,
    maximum: int,
    cutoff: int,
    offer_memory: bool,
    issue_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The canonical strict schema, in the OpenAI `json_schema` dialect.

    `memory_refresh` is offered only when a refresh is actually due. Asking for one on
    every decision would spend tokens rewriting a summary nobody needed rewritten.
    """
    properties: dict[str, Any] = {
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
    }
    if issue_ids:
        properties["wake_hints"] = _guidance(issue_ids, minimum=minimum, maximum=maximum)
    if offer_memory:
        properties["memory_refresh"] = _memory(cutoff)
    return {
        "type": "object",
        "properties": properties,
        # Strict mode requires every property to be listed, so optionality is expressed
        # by the nullable union rather than by omission.
        "required": list(properties),
        "additionalProperties": False,
    }


def narrative_schema() -> dict[str, Any]:
    """The closing report's shape: three pieces of text and nothing else.

    There is no action vocabulary here, no sleep, no memory, and no closure field. The
    reporting call cannot propose an effect because the schema gives it nowhere to put
    one — which is a stronger guarantee than asking it not to.
    """
    item = {"type": "string", "maxLength": 500}
    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": MESSAGE_CHARS,
                "description": (
                    "A short account of what actually happened to this order and why "
                    "supervision ended. Describe only what the evidence shows."
                ),
            },
            "learnings": {
                "type": "array",
                "maxItems": NARRATIVE_ITEMS,
                "items": item,
                "description": (
                    "Patterns visible within this run — what needed chasing, what waited "
                    "on whom. Not general advice, and no causal claims the events do not "
                    "support."
                ),
            },
            "feedback": {
                "type": "array",
                "maxItems": NARRATIVE_ITEMS,
                "items": item,
                "description": (
                    "Recommendations for whoever picks this order up next. These are "
                    "suggestions, never a description of work already done."
                ),
            },
        },
        "required": ["summary", "learnings", "feedback"],
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
