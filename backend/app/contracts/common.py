import json
from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StringConstraints,
)

from app.domain.vocabulary import (
    ACTIVITY_DETAILS_BYTES,
    INSTRUCTION_CHARS,
    MESSAGE_CHARS,
    PAYLOAD_BYTES,
    SUBJECT_CHARS,
)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def timestamp_input(value):
    if not isinstance(value, (str, datetime)):
        raise ValueError("timestamp must be an ISO 8601 string with timezone")
    return value


UTCDateTime = Annotated[
    AwareDatetime,
    BeforeValidator(timestamp_input),
    AfterValidator(lambda value: value.astimezone(UTC)),
]
Count = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Message = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MESSAGE_CHARS)
]
Subject = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SUBJECT_CHARS)
]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def bounded_object(
    value: dict[str, JsonValue],
    *,
    max_bytes: int = PAYLOAD_BYTES,
    max_string_chars: int = MESSAGE_CHARS,
) -> dict[str, JsonValue]:
    # UTF-8 byte size, not Python character count. No NaN/Infinity in durable JSON.
    if (
        len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
        > max_bytes
    ):
        raise ValueError(f"JSON object exceeds {max_bytes // 1024} KiB")

    def walk(item, depth=0):
        if depth > 16:
            raise ValueError("JSON nesting exceeds 16 levels")
        if isinstance(item, str) and len(item) > max_string_chars:
            raise ValueError(f"JSON string exceeds {max_string_chars} characters")
        if isinstance(item, dict):
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)

    walk(value)
    return value


JsonObject = Annotated[dict[str, JsonValue], AfterValidator(bounded_object)]

# Record envelopes need room beyond their ingress payload, including full instructions.
# This still bounds stored details at 128 KiB, 4,000 characters per string, and depth 16.
ActivityDetails = Annotated[
    dict[str, JsonValue],
    AfterValidator(
        lambda value: bounded_object(
            value, max_bytes=ACTIVITY_DETAILS_BYTES, max_string_chars=INSTRUCTION_CHARS
        )
    ),
]
