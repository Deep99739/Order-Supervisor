"use client";

import { useMemo, useState } from "react";
import { MessageSquarePlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { submitInstruction } from "@/lib/api";
import { useCommand } from "@/lib/use-command";
import { effectivePolicy } from "@/lib/policy";
import { INSTRUCTION_LIMIT } from "@/lib/display";
import type { PolicyChanges, RunSnapshot } from "@/lib/contracts";

type Control = {
  id: "speed" | "escalate" | "review";
  label: string;
  sentence: string;
  detail: string;
  changes: PolicyChanges;
};

/**
 * The three controls the system genuinely enforces. Everything else an operator writes
 * is standing guidance the agent reads — real, but not a machine-checked rule, and this
 * dialog says which is which rather than implying English compiles into policy.
 */
const CONTROLS: Control[] = [
  {
    id: "speed",
    label: "Prioritize speed over cost",
    sentence: "For this order, prioritize speed over cost.",
    detail: "Reviews come sooner while a concern is open.",
    changes: { prioritize_speed: true },
  },
  {
    id: "escalate",
    label: "Escalate shipment delays immediately",
    sentence: "If shipment is delayed, escalate immediately.",
    detail: "A recorded delay wakes the agent instead of waiting for the timer.",
    changes: { escalate_shipment_delays: true },
  },
  {
    id: "review",
    label: "Do not contact the customer without human review",
    sentence: "Do not contact the customer without human review.",
    detail: "Customer messages become drafts that a person approves. Enforced, not advisory.",
    changes: { require_customer_review: true },
  },
];

export function InstructionDialog({
  runId,
  snapshot,
  disabled,
  disabledReason,
  onSubmitted,
}: {
  runId: string;
  snapshot: RunSnapshot;
  disabled: boolean;
  disabledReason: string;
  onSubmitted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<Control["id"][]>([]);
  const [text, setText] = useState("");
  const command = useCommand();

  const policy = useMemo(() => effectivePolicy(snapshot), [snapshot]);

  const sentences = CONTROLS.filter((control) =>
    chosen.includes(control.id),
  ).map((control) => control.sentence);
  const body = [...sentences, text.trim()].filter(Boolean).join("\n");
  const tooLong = body.length > INSTRUCTION_LIMIT;

  // The backend holds customer messages for review when an instruction leaves the
  // question unanswered. Saying so here is the difference between a deliberate hold
  // and something that looks broken later.
  const willHoldCustomerContact =
    !chosen.includes("review") &&
    text.trim().length > 0 &&
    !policy.requireCustomerReview;

  function toggle(id: Control["id"], enabled: boolean) {
    setChosen((current) =>
      enabled ? [...current, id] : current.filter((item) => item !== id),
    );
  }

  function reset() {
    setChosen([]);
    setText("");
    command.restart();
  }

  async function send() {
    if (!body || tooLong) return;
    const changes: PolicyChanges = {};
    for (const control of CONTROLS) {
      if (chosen.includes(control.id)) Object.assign(changes, control.changes);
    }
    const sent = await command.send((commandId) =>
      submitInstruction(runId, {
        command_id: commandId,
        operation: "add",
        text: body,
        policy_changes: Object.keys(changes).length > 0 ? changes : null,
      }),
    );
    if (sent) {
      onSubmitted();
      setOpen(false);
      reset();
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) command.restart();
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="outline"
          className="h-10"
          disabled={disabled}
          title={disabledReason}
        >
          <MessageSquarePlus className="size-4" aria-hidden="true" />
          Add instruction
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an instruction to this run</DialogTitle>
          <DialogDescription>
            It applies to this order only, and it survives memory compaction and
            history continuation.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-5">
          <fieldset className="space-y-3">
            <legend className="text-[13px] font-medium">
              Enforced controls
            </legend>
            {CONTROLS.map((control) => (
              <div key={control.id} className="flex items-start gap-3">
                <Checkbox
                  id={`instruction-${control.id}`}
                  className="mt-0.5"
                  checked={chosen.includes(control.id)}
                  onCheckedChange={(checked) =>
                    toggle(control.id, checked === true)
                  }
                />
                <div>
                  <Label htmlFor={`instruction-${control.id}`}>
                    {control.label}
                  </Label>
                  <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
                    {control.detail}
                  </p>
                </div>
              </div>
            ))}
          </fieldset>

          <div className="space-y-2">
            <Label htmlFor="instruction-text">Additional guidance</Label>
            <Textarea
              id="instruction-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Anything else the supervisor should keep in mind for this order."
              className="min-h-24"
            />
            <p
              className="text-[13px] text-muted-foreground tabular-nums"
              data-numeric
            >
              {body.length} of {INSTRUCTION_LIMIT} characters. Free text is standing
              guidance the agent reads; it is not a machine-enforced rule.
            </p>
            {tooLong ? (
              <p className="text-[13px] text-destructive">
                This instruction is too long. Shorten it — nothing is truncated for
                you.
              </p>
            ) : null}
          </div>

          {willHoldCustomerContact ? (
            <p className="rounded-lg bg-hold-surface p-3 text-[13px] leading-5 text-hold">
              This text does not say where you stand on customer contact, so
              customer messages will be held for approval until you set that control
              explicitly. Permission is never inferred from free text.
            </p>
          ) : null}

          {command.error ? (
            <div className="rounded-lg bg-alert-surface p-3 text-[13px] leading-5 text-destructive">
              <p>{command.error.message}</p>
              <p className="mt-1">
                Your text is unchanged and the same command identity is kept, so
                sending again is a retry.
              </p>
            </div>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            className="h-10"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            className="h-10"
            onClick={send}
            disabled={!body || tooLong || command.status === "sending"}
          >
            {command.status === "sending"
              ? "Sending…"
              : command.status === "failed"
                ? "Retry"
                : "Add instruction"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
