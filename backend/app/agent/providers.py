"""One narrow provider interface, and the two adapters this repository was configured for.

The interface is small on purpose: hand it a system prompt, a context, and the shape of
the answer, and it returns the raw text plus whatever usage the provider actually
reported. The model gets no database, no Temporal client, no code execution, no browsing,
and no way to send anything anywhere. Its entire influence on the world is the JSON it
returns and what the workflow subsequently authorises.

On retries. A decision episode is allowed two model answers, and the workflow owns that
budget — SDK retries are disabled for the activity. Separately, a request the provider
*refused* (rate limit, transient outage) produces no answer at all, so it may be reissued
against the next configured key. That rotation is bounded and counted, and it never buys
a third opinion.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import SecretStr

from app.domain.vocabulary import PROVIDER_KEYS_PER_ATTEMPT, PROVIDER_TIMEOUT_SECONDS

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class ProviderError(Exception):
    """A provider call that produced no usable answer.

    `retryable` distinguishes "the provider was unavailable" from "the provider rejected
    this request", which are different operator problems.
    """

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderReply:
    text: str
    model_label: str
    # Only what the provider actually returned. A timeout can still cost tokens, but a
    # number nobody reported is not evidence of anything.
    usage: dict[str, int] = field(default_factory=dict)
    transport_attempts: int = 1


def _tokens(source: dict[str, Any] | None, *names: tuple[str, str]) -> dict[str, int]:
    if not source:
        return {}
    usage: dict[str, int] = {}
    for label, key in names:
        value = source.get(key)
        if isinstance(value, int):
            usage[label] = value
    return usage


class Provider:
    """Base adapter. Subclasses describe one HTTP shape and nothing else."""

    name = ""

    def __init__(self, *, model: str, keys: tuple[SecretStr, ...]):
        if not model.strip():
            raise ProviderError("No model name is configured.", retryable=False)
        if not keys:
            raise ProviderError(
                "No API key is configured for the selected provider.", retryable=False
            )
        self.model = model.strip()
        self.keys = keys

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def _request(self, key: SecretStr, system: str, user: str, schema: dict) -> dict[str, Any]:
        raise NotImplementedError

    def _reply(self, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
        raise NotImplementedError

    async def complete(self, *, system: str, user: str, schema: dict) -> ProviderReply:
        last: ProviderError | None = None
        usable = self.keys[:PROVIDER_KEYS_PER_ATTEMPT]
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS) as client:
            for index, key in enumerate(usable, start=1):
                call = self._request(key, system, user, schema)
                try:
                    response = await client.post(
                        call["url"], headers=call["headers"], json=call["body"]
                    )
                except httpx.HTTPError as error:
                    # Never surface the exception body: it can carry the request headers.
                    last = ProviderError(
                        f"The {self.name} endpoint could not be reached "
                        f"({type(error).__name__}).",
                        retryable=True,
                    )
                    continue
                if response.status_code in RETRYABLE_STATUS:
                    last = ProviderError(
                        f"{self.name} refused the request with HTTP {response.status_code}"
                        f" ({_reason(response)}).",
                        retryable=True,
                    )
                    continue
                if response.status_code >= 400:
                    raise ProviderError(
                        f"{self.name} rejected the request with HTTP {response.status_code}"
                        f" ({_reason(response)}).",
                        retryable=False,
                    )
                text, usage = self._reply(response.json())
                if not text.strip():
                    raise ProviderError(
                        f"{self.name} returned an empty answer.", retryable=False
                    )
                return ProviderReply(
                    text=text, model_label=self.label, usage=usage, transport_attempts=index
                )
        raise last or ProviderError(f"{self.name} produced no answer.", retryable=True)


def _reason(response: httpx.Response) -> str:
    """A short, safe description of a failure. Never echoes what was sent."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:200] or "no detail"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("status") or "no detail")[:200]
    return str(error or "no detail")[:200]


class GroqProvider(Provider):
    """Groq's OpenAI-compatible chat completions with a strict JSON schema."""

    name = "groq"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def _request(self, key: SecretStr, system: str, user: str, schema: dict) -> dict[str, Any]:
        return {
            "url": self.endpoint,
            "headers": {"authorization": f"Bearer {key.get_secret_value()}"},
            "body": {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "decision", "strict": True, "schema": schema},
                },
            },
        }

    def _reply(self, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError("groq returned no choices.", retryable=False)
        content = choices[0].get("message", {}).get("content") or ""
        return content, _tokens(
            payload.get("usage"), ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        )


class GoogleProvider(Provider):
    """Gemini's generateContent with a response schema in its OpenAPI subset."""

    name = "google"
    host = "https://generativelanguage.googleapis.com/v1beta/models"

    def _request(self, key: SecretStr, system: str, user: str, schema: dict) -> dict[str, Any]:
        return {
            "url": f"{self.host}/{self.model}:generateContent",
            "headers": {"x-goog-api-key": key.get_secret_value()},
            "body": {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                },
            },
        }

    def _reply(self, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
        candidates = payload.get("candidates") or []
        if not candidates:
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(
                f"google returned no candidate ({blocked or 'no detail'}).", retryable=False
            )
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        return text, _tokens(
            payload.get("usageMetadata"), ("input_tokens", "promptTokenCount"),
            ("output_tokens", "candidatesTokenCount"),
        )


ADAPTERS = {GroqProvider.name: GroqProvider, GoogleProvider.name: GoogleProvider}


def build_provider(provider: str, model: str, keys: tuple[SecretStr, ...]) -> Provider:
    adapter = ADAPTERS.get(provider)
    if adapter is None:
        raise ProviderError(
            f"No adapter for provider {provider or '(unset)'}; "
            f"MODEL_PROVIDER must be one of {', '.join(sorted(ADAPTERS))}.",
            retryable=False,
        )
    return adapter(model=model, keys=keys)


def parse_json(text: str) -> dict[str, Any]:
    """Read the model's answer, tolerating a fenced block but nothing more creative."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        body = body.rsplit("```", 1)[0]
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError(f"The answer was not valid JSON: {error}.", retryable=False) from None
    if not isinstance(parsed, dict):
        raise ProviderError("The answer was valid JSON but not an object.", retryable=False)
    return parsed
