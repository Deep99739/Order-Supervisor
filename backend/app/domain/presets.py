"""Three presets whose behaviour actually differs, not three names.

Stable identifiers let seeding be repeatable without overwriting operator edits.
"""

from uuid import UUID

from app.contracts.supervisor import SupervisorConfig, WakeProfile
from app.domain.vocabulary import DEMO_MAXIMUM_AGE_SECONDS, DEMO_WAKE, ActionName

ALL_ACTIONS = list(ActionName)

STANDARD_ID = UUID("2f8f7f1a-0c47-4b21-9a1f-3d9c0b6a5e01")
URGENT_ID = UUID("2f8f7f1a-0c47-4b21-9a1f-3d9c0b6a5e02")
REVIEW_FIRST_ID = UUID("2f8f7f1a-0c47-4b21-9a1f-3d9c0b6a5e03")

PRESETS: tuple[SupervisorConfig, ...] = (
    SupervisorConfig(
        id=STANDARD_ID,
        version=1,
        name="Standard order care",
        base_instructions=(
            "Supervise this order from payment through delivery. Record what is actually known, "
            "follow up with the responsible team when an issue is genuinely blocking progress, "
            "and wait quietly when the order is progressing normally. Contact the customer only "
            "when they need information they do not already have."
        ),
        allowed_actions=ALL_ACTIONS,
        wake_profile=WakeProfile(mode="standard"),
        maximum_age_seconds=86400,
        customer_review_default=False,
        escalate_shipment_delays=False,
        prioritize_speed=False,
    ),
    SupervisorConfig(
        id=URGENT_ID,
        version=1,
        name="Urgent fulfillment",
        base_instructions=(
            "This order is time critical. Prefer timely operational follow-up over waiting for "
            "more evidence, review the order again sooner than usual while any issue is open, and "
            "escalate a shipment delay to logistics as soon as it is recorded. Prioritising speed "
            "does not authorise contacting the same team repeatedly about unchanged work."
        ),
        allowed_actions=ALL_ACTIONS,
        # A shorter permitted review horizon, still inside the standard profile bounds.
        wake_profile=WakeProfile(
            mode="standard", minimum_seconds=30, default_seconds=120, maximum_seconds=900
        ),
        maximum_age_seconds=43200,
        customer_review_default=False,
        escalate_shipment_delays=True,
        prioritize_speed=True,
    ),
    SupervisorConfig(
        id=REVIEW_FIRST_ID,
        version=1,
        name="Customer review first",
        base_instructions=(
            "Supervise this order normally, but treat customer contact as something a person "
            "approves. Prepare a clear, specific draft when the customer genuinely needs an "
            "update and continue internal follow-up while that draft waits for review."
        ),
        allowed_actions=ALL_ACTIONS,
        wake_profile=WakeProfile(mode="standard"),
        maximum_age_seconds=86400,
        customer_review_default=True,
        escalate_shipment_delays=False,
        prioritize_speed=False,
    ),
)


def demo_timing(config: SupervisorConfig, preset: str | None) -> SupervisorConfig:
    """Return the frozen configuration this run should snapshot.

    A demo preset shortens timing for one run only. It never changes permissions,
    review policy, or any lifecycle check.
    """
    if preset is None:
        return config
    maximum_age = DEMO_MAXIMUM_AGE_SECONDS if preset == "short_review" else 300
    # Rebuild rather than copy so the demo profile and age revalidate together.
    return SupervisorConfig.model_validate(
        config.model_dump()
        | {
            "wake_profile": {
                "mode": "demo",
                "minimum_seconds": DEMO_WAKE[0],
                "default_seconds": DEMO_WAKE[1],
                "maximum_seconds": DEMO_WAKE[2],
            },
            "maximum_age_seconds": maximum_age,
        }
    )
