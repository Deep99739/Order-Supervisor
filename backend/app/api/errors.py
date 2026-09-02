"""Typed error responses.

Every failure names a machine-readable `code`, says whether retrying can help, and keeps
the identifiers a client needs in order to retry with the same identity. Driver and
provider text never reaches a public response.
"""

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.contracts.commands import ApiError

MESSAGE_LIMIT = 500


class ApiFailure(Exception):
    """Raised by a route to return one typed error."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        field_details: dict[str, str] | None = None,
        command_id: UUID | None = None,
        run_id: UUID | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error = ApiError(
            code=code,
            message=message[:MESSAGE_LIMIT],
            retryable=retryable,
            field_details=field_details or {},
            command_id=command_id,
            run_id=run_id,
        )


def install_error_handlers(api: FastAPI) -> None:
    @api.exception_handler(ApiFailure)
    async def typed_failure(_: Request, failure: ApiFailure) -> JSONResponse:
        return JSONResponse(
            failure.error.model_dump(mode="json"), status_code=failure.status_code
        )

    @api.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, failure: RequestValidationError) -> JSONResponse:
        details: dict[str, str] = {}
        for item in failure.errors():
            location = ".".join(str(part) for part in item["loc"] if part != "body")
            details[location or "body"] = str(item["msg"])[:MESSAGE_LIMIT]
        error = ApiError(
            code="invalid_request",
            message="The request could not be validated; no command was accepted.",
            retryable=False,
            field_details=details,
        )
        return JSONResponse(error.model_dump(mode="json"), status_code=422)
