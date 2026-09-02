from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import PositiveInt, Reference, WireModel
from app.domain.vocabulary import (
    DEMO_MAXIMUM_AGE_SECONDS,
    DEMO_WAKE,
    STANDARD_WAKE,
    ActionName,
)


class WakeProfile(WireModel):
    mode: Literal["standard", "demo"] = "standard"
    minimum_seconds: PositiveInt = STANDARD_WAKE[0]
    default_seconds: PositiveInt = STANDARD_WAKE[1]
    maximum_seconds: PositiveInt = STANDARD_WAKE[2]

    @model_validator(mode="before")
    @classmethod
    def demo_defaults(cls, value):
        if isinstance(value, dict) and value.get("mode") == "demo":
            return {
                "minimum_seconds": DEMO_WAKE[0],
                "default_seconds": DEMO_WAKE[1],
                "maximum_seconds": DEMO_WAKE[2],
                **value,
            }
        return value

    @model_validator(mode="after")
    def ordered_bounds(self):
        lower, _, upper = DEMO_WAKE if self.mode == "demo" else STANDARD_WAKE
        if (
            not lower
            <= self.minimum_seconds
            <= self.default_seconds
            <= self.maximum_seconds
            <= upper
        ):
            raise ValueError("wake intervals must be ordered and within the selected profile")
        return self


class SupervisorConfig(WireModel):
    id: UUID
    version: PositiveInt
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    base_instructions: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    allowed_actions: Annotated[list[ActionName], Field(max_length=5)]
    wake_profile: WakeProfile = Field(default_factory=WakeProfile)
    maximum_age_seconds: Annotated[int, Field(strict=True, ge=60, le=2592000)] = 86400
    customer_review_default: bool = False
    escalate_shipment_delays: bool = False
    prioritize_speed: bool = False
    model_label: Reference | None = None

    @model_validator(mode="before")
    @classmethod
    def demo_age_default(cls, value):
        if not isinstance(value, dict) or "maximum_age_seconds" in value:
            return value
        profile = value.get("wake_profile")
        mode = (
            profile.mode
            if isinstance(profile, WakeProfile)
            else profile.get("mode")
            if isinstance(profile, dict)
            else None
        )
        if mode == "demo":
            return {**value, "maximum_age_seconds": DEMO_MAXIMUM_AGE_SECONDS}
        return value

    @model_validator(mode="after")
    def unique_actions_and_age(self):
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed action names must be unique")
        if self.wake_profile.mode == "demo" and self.maximum_age_seconds > DEMO_MAXIMUM_AGE_SECONDS:
            raise ValueError("demo maximum age cannot exceed 30 minutes")
        return self
