"""Expose agreed DTO schemas without pretending unimplemented routes exist."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from app.contracts.commands import (
    KNOWN_PAYLOADS,
    ApiError,
    CommandAcknowledgement,
    ControlCommand,
    CreateRunRequest,
    EventCommand,
    InstructionCommand,
    ReviewCommand,
)
from app.contracts.decision import DecisionProposal
from app.contracts.run import ActivityRecord, FinalOutput, RunSnapshot
from app.contracts.supervisor import SupervisorConfig

PUBLIC_MODELS = (
    SupervisorConfig,
    CreateRunRequest,
    EventCommand,
    InstructionCommand,
    ControlCommand,
    ReviewCommand,
    CommandAcknowledgement,
    RunSnapshot,
    ActivityRecord,
    DecisionProposal,
    FinalOutput,
    ApiError,
    *KNOWN_PAYLOADS.values(),
)


def install_contract_schemas(api: FastAPI) -> None:
    def openapi():
        if api.openapi_schema is None:
            schema = get_openapi(title=api.title, version=api.version, routes=api.routes)
            _, shared = models_json_schema(
                [(model, "validation") for model in PUBLIC_MODELS],
                ref_template="#/components/schemas/{model}",
            )
            schemas = schema.setdefault("components", {}).setdefault("schemas", {})
            # Routed models already published by FastAPI win; this only fills the gaps.
            for name, definition in shared["$defs"].items():
                schemas.setdefault(name, definition)
            schema["x-order-supervisor-contract-version"] = 1
            api.openapi_schema = schema
        return api.openapi_schema

    api.openapi = openapi
