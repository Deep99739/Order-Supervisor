"""One canonical JSON digest, used for request and dedupe identity."""

import json
from hashlib import sha256

from pydantic import JsonValue


def canonical_digest(value: JsonValue) -> str:
    """Stable SHA-256 over sorted, separator-normalized JSON.

    Two requests carrying the same content produce the same digest, so a retry is
    recognizable; changed content under a reused identity is a conflict, not a retry.
    """
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return sha256(encoded.encode()).hexdigest()
