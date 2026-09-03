/**
 * A read-only mirror of `backend/app/domain/policy.py::effective_policy`.
 *
 * The backend remains the authority: this never changes what a run does, it only lets
 * the console state the rule the operator is currently living under. In particular it
 * reproduces the conservative fallback — an instruction whose stance on customer contact
 * is unstated turns customer messages into drafts until someone answers that question
 * with the named control. Showing that as "review required" without saying *why* would
 * look like a bug rather than a deliberate hold.
 *
 * If the Python rule changes, change this with it.
 */
import type { ActiveInstruction, SupervisorConfig } from "./contracts";

export type EffectivePolicy = {
  prioritizeSpeed: boolean;
  escalateShipmentDelays: boolean;
  requireCustomerReview: boolean;
  /** Review is on because free text left the question open, not because it was asked for. */
  reviewFromAmbiguity: boolean;
};

export function effectivePolicy(run: {
  supervisor: SupervisorConfig;
  instructions: ActiveInstruction[];
}): EffectivePolicy {
  let prioritizeSpeed = run.supervisor.prioritize_speed;
  let escalateShipmentDelays = run.supervisor.escalate_shipment_delays;
  let review = run.supervisor.customer_review_default;
  let reviewStated = false;
  let unclassified = false;

  for (const instruction of run.instructions) {
    const changes = instruction.policy_changes;
    if (!changes) {
      unclassified = true;
      continue;
    }
    if (changes.prioritize_speed !== null && changes.prioritize_speed !== undefined) {
      prioritizeSpeed = changes.prioritize_speed;
    }
    if (
      changes.escalate_shipment_delays !== null &&
      changes.escalate_shipment_delays !== undefined
    ) {
      escalateShipmentDelays = changes.escalate_shipment_delays;
    }
    if (
      changes.require_customer_review === null ||
      changes.require_customer_review === undefined
    ) {
      unclassified = true;
    } else {
      review = changes.require_customer_review;
      reviewStated = true;
    }
  }

  const ambiguous = unclassified && !reviewStated && !review;
  return {
    prioritizeSpeed,
    escalateShipmentDelays,
    requireCustomerReview: review || ambiguous,
    reviewFromAmbiguity: ambiguous,
  };
}
