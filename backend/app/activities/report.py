"""The closing-report boundary.

One bounded call, asking for prose over evidence the model cannot change. It has no
action vocabulary, no memory refresh, no timing, and no way to influence why the run
ended — the schema simply gives it nowhere to put any of that.

Everything here is best-effort by design. A provider that is unavailable, slow, or
unusable costs the run a paragraph, never its report: the workflow already holds a
complete factual version before this is called, and keeps it on any failure. That is why
nothing in this module raises for the caller to retry.
"""

from typing import Any

from temporalio import activity

from app.agent.prompt import REPORTING, report_prompt
from app.agent.providers import ProviderError, build_provider, parse_json
from app.agent.schema import narrative_schema, to_openapi
from app.config import Settings
from app.contracts.decision import ProviderUsage
from app.contracts.report import ReportNarrative, ReportRequest, ReportResult
from app.domain import reporting


class ReportActivities:
    def __init__(self, settings: Settings):
        self.settings = settings

    @activity.defn(name="write_report")
    async def write_report(self, request: dict[str, Any]) -> dict[str, Any]:
        parsed = ReportRequest.model_validate(request)
        if self.settings.agent_mode == "scripted":
            # A stand-in is never presented as a model-written report, so scripted mode
            # simply declines and the factual version stands on its own terms.
            return ReportResult(
                provenance="factual_fallback",
                limitation="Scripted mode: no model was asked to write this report.",
            ).model_dump(mode="json")
        return (await self._write(parsed)).model_dump(mode="json")

    async def _write(self, request: ReportRequest) -> ReportResult:
        settings = self.settings
        try:
            provider = build_provider(
                settings.model_provider, settings.model_name, settings.api_keys
            )
        except ProviderError as error:
            return _declined(f"No model is configured for reporting: {error}")

        schema = narrative_schema()
        if provider.name == "google":
            schema = to_openapi(schema)

        try:
            reply = await provider.complete(
                system=REPORTING, user=report_prompt(request), schema=schema
            )
        except ProviderError as error:
            return _declined(f"The closing narrative could not be generated: {error}", attempts=1)

        try:
            narrative = ReportNarrative.model_validate(_clean(parse_json(reply.text)))
        except ProviderError as error:
            return _declined(f"The closing narrative was unreadable: {error}", attempts=1)
        except Exception as error:  # noqa: BLE001 - reported to the operator as-is
            return _declined(
                f"The closing narrative did not satisfy its contract: {error}", attempts=1
            )

        # The record gets the final say. A narrative that disagrees with the facts is
        # discarded here rather than being softened into something half true.
        disagreements = reporting.contradictions(
            narrative, request.snapshot, list(request.committed)
        )
        if disagreements:
            activity.logger.info(
                "report for %s rejected: %s", request.run_id, "; ".join(disagreements)
            )
            return _declined(
                "The closing narrative contradicted the record: " + "; ".join(disagreements),
                attempts=1,
            )

        return ReportResult(
            narrative=narrative,
            provenance="model_assisted",
            model_label=provider.label,
            usage=ProviderUsage(
                input_tokens=reply.usage.get("input_tokens"),
                output_tokens=reply.usage.get("output_tokens"),
                transport_attempts=reply.transport_attempts,
            ),
            attempts=1,
        )


def _declined(limitation: str, *, attempts: int = 0) -> ReportResult:
    return ReportResult(
        provenance="factual_fallback", limitation=limitation[:500], attempts=attempts
    )


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the three fields this call is allowed to fill, and drop everything else.

    A model that returns an `actions` array here is not refused for it; the field simply
    does not exist on the way in, so it cannot become anything.
    """
    known = set(ReportNarrative.model_fields)
    cleaned = {key: value for key, value in payload.items() if key in known and value is not None}
    for field in ("learnings", "feedback"):
        items = cleaned.get(field)
        cleaned[field] = (
            [str(item) for item in items if isinstance(item, str) and item.strip()]
            if isinstance(items, list)
            else []
        )
    return cleaned
