"""Validated process settings. Secrets stay outside workflow payloads.

One credential is enough to run this. Several are supported because a free provider tier
runs out of quota quickly, and rotating to the next key on a refusal keeps a
demonstration going without pretending the refusal did not happen. Keys are discovered by
prefix — `GROQ_API_KEY`, `GOOGLE_API_KEY`, or any numbered suffix of those — which is why
unknown environment entries are ignored here rather than rejected.
"""

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVIDERS = ("groq", "google")
KEY_NAME = re.compile(r"^(GROQ|GOOGLE)_API_KEY(?:_(\d+))?$")


def _env_file_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, _, value = entry.partition("=")
        values[name.strip()] = value.strip().strip("'\"")
    return values


def discover_api_keys(provider: str, *, env_file: Path = Path(".env")) -> tuple[SecretStr, ...]:
    """Collect every configured key for one provider, in a stable order.

    The process environment wins over the file, as it does everywhere else.
    """
    if provider not in PROVIDERS:
        return ()
    found: dict[tuple[int, str], str] = {}
    for name, value in {**_env_file_values(env_file), **os.environ}.items():
        match = KEY_NAME.match(name)
        if not match or match.group(1).lower() != provider or not value.strip():
            continue
        found[(int(match.group(2) or 0), name)] = value.strip()
    return tuple(SecretStr(found[key]) for key in sorted(found))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "order-supervisor"
    allowed_ui_origin: str = "http://localhost:3000"
    agent_mode: Literal["live", "scripted"] = "live"
    model_provider: Literal["", "groq", "google"] = ""
    model_name: str = ""
    model_api_key: SecretStr = SecretStr("")
    demo_mode: bool = False

    @field_validator("database_url")
    @classmethod
    def postgres_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.path.strip("/")
        ):
            raise ValueError("DATABASE_URL must name a PostgreSQL host and database")
        return value

    @field_validator("allowed_ui_origin")
    @classmethod
    def explicit_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ALLOWED_UI_ORIGIN must be one explicit HTTP origin")
        return value.rstrip("/")

    @field_validator("temporal_address")
    @classmethod
    def temporal_host_port(cls, value: str) -> str:
        parsed = urlsplit(f"//{value}")
        if not parsed.hostname or not parsed.port or parsed.username or parsed.path:
            raise ValueError("TEMPORAL_ADDRESS must be host:port")
        return value

    @field_validator("temporal_namespace", "temporal_task_queue")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip() or len(value) > 200:
            raise ValueError("must contain 1 to 200 characters")
        return value.strip()

    @property
    def api_keys(self) -> tuple[SecretStr, ...]:
        """Every usable credential for the selected provider, explicit one first."""
        explicit = self.model_api_key.get_secret_value().strip()
        discovered = discover_api_keys(self.model_provider)
        if not explicit:
            return discovered
        rest = tuple(key for key in discovered if key.get_secret_value() != explicit)
        return (SecretStr(explicit), *rest)

    @property
    def live_model_configured(self) -> bool:
        return bool(self.model_provider and self.model_name.strip() and self.api_keys)


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        fields = ", ".join(".".join(map(str, item["loc"])) for item in error.errors())
        raise RuntimeError(
            f"Invalid configuration fields: {fields}. See backend/.env.example."
        ) from None
