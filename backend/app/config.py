"""Validated process settings. Secrets stay outside workflow payloads."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid", hide_input_in_errors=True)

    database_url: SecretStr
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "order-supervisor"
    allowed_ui_origin: str = "http://localhost:3000"
    agent_mode: Literal["live", "scripted"] = "live"
    model_provider: str = ""
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
    def live_model_configured(self) -> bool:
        return all(
            (
                self.model_provider.strip(),
                self.model_name.strip(),
                self.model_api_key.get_secret_value().strip(),
            )
        )


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        fields = ", ".join(".".join(map(str, item["loc"])) for item in error.errors())
        raise RuntimeError(
            f"Invalid configuration fields: {fields}. See backend/.env.example."
        ) from None
