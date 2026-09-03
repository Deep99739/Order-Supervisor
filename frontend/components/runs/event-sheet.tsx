"use client";

import { useMemo, useState } from "react";
import { Radio } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { StateBadge } from "@/components/state-badge";
import { Textarea } from "@/components/ui/textarea";
import { submitEvent } from "@/lib/api";
import { newEventId } from "@/lib/ids";
import { useCommand } from "@/lib/use-command";
import {
  DISPOSITION_LABEL,
  DISPOSITION_TONE,
  eventLabel,
  MESSAGE_LIMIT,
} from "@/lib/display";
import type { ActivityRecord, JsonObject } from "@/lib/contracts";

type FieldKind = "text" | "message" | "number" | "timestamp";

type Field = {
  key: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  help?: string;
  placeholder?: string;
};

type Preset = {
  type: string;
  label: string;
  note?: string;
  fields: Field[];
  /** Extra validation the API will apply, stated before the request is sent. */
  check?: (values: Record<string, string>) => string | null;
};

const PRESETS: Preset[] = [
  {
    type: "payment_confirmed",
    label: "Payment confirmed",
    fields: [
      { key: "payment_reference", label: "Payment reference", kind: "text" },
      { key: "attempt_reference", label: "Attempt reference", kind: "text" },
    ],
  },
  {
    type: "payment_failed",
    label: "Payment failed",
    fields: [
      { key: "reason", label: "Reason", kind: "message", required: true },
      { key: "attempt_reference", label: "Attempt reference", kind: "text" },
    ],
    note: "A failure for an attempt that does not match a confirmed payment is kept for review instead of reversing the recorded fact.",
  },
  {
    type: "shipment_created",
    label: "Shipment created",
    fields: [
      {
        key: "shipment_reference",
        label: "Shipment reference",
        kind: "text",
        required: true,
        placeholder: "SHP-4410",
      },
      { key: "carrier", label: "Carrier", kind: "text" },
      {
        key: "expected_at",
        label: "Expected delivery",
        kind: "timestamp",
        help: "ISO 8601 with a timezone, e.g. 2026-09-04T17:00:00Z",
      },
    ],
  },
  {
    type: "shipment_delayed",
    label: "Shipment delayed",
    fields: [
      { key: "reason", label: "Reason", kind: "message", required: true },
      { key: "shipment_reference", label: "Shipment reference", kind: "text" },
      { key: "expected_at", label: "New expected delivery", kind: "timestamp" },
    ],
  },
  {
    type: "delivered",
    label: "Delivered",
    fields: [
      { key: "delivered_at", label: "Delivered at", kind: "timestamp" },
      { key: "evidence_reference", label: "Delivery evidence", kind: "text" },
    ],
    note: "Delivery closes supervision under the workflow's own rule.",
    check: (values) =>
      values.delivered_at?.trim() || values.evidence_reference?.trim()
        ? null
        : "Delivery needs a timestamp or an evidence reference.",
  },
  {
    type: "refund_requested",
    label: "Refund requested",
    fields: [
      { key: "reason", label: "Reason", kind: "message", required: true },
      { key: "customer_reference", label: "Customer reference", kind: "text" },
    ],
  },
  {
    type: "customer_message_received",
    label: "Customer message",
    fields: [
      {
        key: "message",
        label: "What the customer wrote",
        kind: "message",
        required: true,
      },
    ],
    note: "Customer text is recorded as evidence. It never becomes an instruction to the agent.",
  },
  {
    type: "no_update_for_n_hours",
    label: "No update reported",
    fields: [
      {
        key: "hours",
        label: "Hours without an update",
        kind: "number",
        required: true,
        placeholder: "48",
        help: "Measured against recorded order progress, not against the last event.",
      },
    ],
    check: (values) => {
      const hours = Number(values.hours);
      return Number.isFinite(hours) && hours > 0 && hours <= 8760
        ? null
        : "Hours must be a positive number up to 8760.";
    },
  },
  {
    type: "order_created",
    label: "Order created",
    fields: [],
    note: "Creation was already recorded when the run started. Sending this again demonstrates that a redundant event changes nothing.",
  },
];

const CUSTOM = "__custom__";

function payloadOf(
  preset: Preset,
  values: Record<string, string>,
): JsonObject {
  const payload: JsonObject = {};
  for (const field of preset.fields) {
    const raw = values[field.key]?.trim();
    if (!raw) continue;
    payload[field.key] = field.kind === "number" ? Number(raw) : raw;
  }
  return payload;
}

/**
 * The event generator. It sends the same command the rest of the system sends — it never
 * writes to the database behind the workflow, and an unfamiliar type is accepted as
 * evidence rather than rejected for being unknown.
 */
export function EventSheet({
  runId,
  records,
  disabled,
  disabledReason,
  onSubmitted,
}: {
  runId: string;
  records: ActivityRecord[];
  disabled: boolean;
  disabledReason: string;
  onSubmitted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<string>(PRESETS[0].type);
  const [values, setValues] = useState<Record<string, string>>({});
  const [customType, setCustomType] = useState("");
  const [customJson, setCustomJson] = useState("{\n  \n}");
  const [eventId, setEventId] = useState(() => newEventId());
  const command = useCommand();

  const preset = PRESETS.find((item) => item.type === type) ?? null;

  const receipt = useMemo(
    () =>
      records.find(
        (record) =>
          record.command_id === command.commandId && record.kind === "event",
      ) ?? null,
    [records, command.commandId],
  );

  const customProblem = useMemo(() => {
    if (preset) return null;
    if (!/^[a-z][a-z0-9_]*$/.test(customType.trim())) {
      return "An event type is lowercase letters, digits, and underscores.";
    }
    try {
      const parsed: unknown = JSON.parse(customJson);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return "The payload must be a JSON object.";
      }
      if (new TextEncoder().encode(customJson).length > 8192) {
        return "The payload must stay under 8 KiB.";
      }
    } catch {
      return "That is not valid JSON.";
    }
    return null;
  }, [preset, customType, customJson]);

  const problem = preset
    ? preset.fields.some(
        (field) => field.required && !values[field.key]?.trim(),
      )
      ? "Fill in the required fields."
      : (preset.check?.(values) ?? null)
    : customProblem;

  /**
   * Editing anything after a submission has settled starts a new one. The previous
   * identity is never reused for different content, and the previous receipt stops being
   * shown beside a form it no longer describes.
   */
  function touch() {
    if (command.status === "accepted") {
      setEventId(newEventId());
      command.restart();
    }
  }

  function edit(key: string, value: string) {
    touch();
    setValues((current) => ({ ...current, [key]: value }));
  }

  function changeType(next: string) {
    touch();
    setType(next);
    setValues({});
  }

  function reset() {
    setValues({});
    setCustomJson("{\n  \n}");
    setEventId(newEventId());
    command.restart();
  }

  function close() {
    setOpen(false);
    // A settled submission ends when the drawer closes; a failed one keeps its identity
    // so reopening and pressing send again is still a retry.
    if (command.status === "accepted") reset();
  }

  async function send() {
    if (problem) return;
    const body = preset
      ? {
          command_id: command.commandId,
          event_id: eventId,
          event_type: preset.type,
          occurred_at: new Date().toISOString(),
          payload: payloadOf(preset, values),
        }
      : {
          command_id: command.commandId,
          event_id: eventId,
          event_type: customType.trim(),
          occurred_at: new Date().toISOString(),
          payload: JSON.parse(customJson) as JsonObject,
        };
    const sent = await command.send(() => submitEvent(runId, body));
    if (sent) onSubmitted();
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => (next ? setOpen(true) : close())}
    >
      <SheetTrigger asChild>
        <Button className="h-10" disabled={disabled} title={disabledReason}>
          <Radio className="size-4" aria-hidden="true" />
          Inject event
        </Button>
      </SheetTrigger>
      <SheetContent aria-describedby="event-sheet-note">
        <SheetHeader>
          <SheetTitle>Send an event into this run</SheetTitle>
          <SheetDescription id="event-sheet-note">
            Simulated order events, delivered as signals through the same API the
            rest of the system uses.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="event-type">Event type</Label>
            <Select value={type} onValueChange={changeType}>
              <SelectTrigger id="event-type" aria-label="Event type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRESETS.map((item) => (
                  <SelectItem key={item.type} value={item.type}>
                    {item.label}
                  </SelectItem>
                ))}
                <SelectItem value={CUSTOM}>Custom or unknown event</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {preset?.note ? (
            <p className="rounded-lg bg-muted p-3 text-[13px] leading-5 text-muted-foreground">
              {preset.note}
            </p>
          ) : null}

          {preset ? (
            preset.fields.map((field) => (
              <div key={field.key} className="space-y-2">
                <Label htmlFor={`event-${field.key}`}>
                  {field.label}
                  {field.required ? "" : " (optional)"}
                </Label>
                {field.kind === "message" ? (
                  <Textarea
                    id={`event-${field.key}`}
                    value={values[field.key] ?? ""}
                    maxLength={MESSAGE_LIMIT}
                    onChange={(change) => edit(field.key, change.target.value)}
                  />
                ) : (
                  <Input
                    id={`event-${field.key}`}
                    type={field.kind === "number" ? "number" : "text"}
                    inputMode={field.kind === "number" ? "decimal" : undefined}
                    maxLength={field.kind === "text" ? 200 : undefined}
                    placeholder={field.placeholder}
                    value={values[field.key] ?? ""}
                    autoComplete="off"
                    onChange={(change) => edit(field.key, change.target.value)}
                  />
                )}
                {field.help ? (
                  <p className="text-[13px] leading-5 text-muted-foreground">
                    {field.help}
                  </p>
                ) : null}
              </div>
            ))
          ) : (
            <>
              <div className="space-y-2">
                <Label htmlFor="event-custom-type">Event type name</Label>
                <Input
                  id="event-custom-type"
                  value={customType}
                  onChange={(change) => {
                    touch();
                    setCustomType(change.target.value);
                  }}
                  placeholder="carrier_lost_parcel"
                  autoComplete="off"
                />
                <p className="text-[13px] leading-5 text-muted-foreground">
                  An unfamiliar type is not rejected. It is recorded as evidence and
                  flagged for review, and a paused run waits rather than guessing.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="event-custom-json">Payload</Label>
                <Textarea
                  id="event-custom-json"
                  value={customJson}
                  onChange={(change) => {
                    touch();
                    setCustomJson(change.target.value);
                  }}
                  className="min-h-32 font-mono text-[13px]"
                  spellCheck={false}
                />
              </div>
            </>
          )}

          {problem && command.status !== "accepted" ? (
            <p className="text-[13px] text-destructive">{problem}</p>
          ) : null}

          {command.error ? (
            <div className="rounded-lg bg-alert-surface p-3 text-[13px] leading-5 text-destructive">
              <p>{command.error.message}</p>
              <p className="mt-1">
                The same event identity is kept, so sending again is a retry rather
                than a second event.
              </p>
            </div>
          ) : null}

          {command.status === "accepted" ? (
            <div className="rounded-lg bg-quiet-surface p-3 text-[13px] leading-5 text-quiet">
              {receipt ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">
                      {eventLabel(
                        typeof receipt.details.event_type === "string"
                          ? receipt.details.event_type
                          : "unknown",
                      )}
                    </span>
                    <StateBadge
                      label={DISPOSITION_LABEL[receipt.disposition]}
                      tone={DISPOSITION_TONE[receipt.disposition]}
                      dot={false}
                      className="px-2 py-0.5 text-[12px]"
                    />
                  </div>
                  <p className="mt-1">{receipt.explanation}</p>
                </>
              ) : (
                <p>Submitted — waiting for the run to process it.</p>
              )}
            </div>
          ) : null}
        </SheetBody>
        <SheetFooter>
          {command.status === "accepted" ? (
            <>
              <Button
                variant="outline"
                className="h-10"
                onClick={close}
              >
                Done
              </Button>
              <Button className="h-10" onClick={reset}>
                Send another event
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                className="h-10"
                onClick={close}
              >
                Cancel
              </Button>
              <Button
                className="h-10"
                onClick={send}
                disabled={Boolean(problem) || command.status === "sending"}
              >
                {command.status === "sending"
                  ? "Sending…"
                  : command.status === "failed"
                    ? "Retry send"
                    : "Send event"}
              </Button>
            </>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
