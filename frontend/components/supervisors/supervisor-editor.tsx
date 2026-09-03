"use client";

import { useMemo, useState } from "react";
import { CircleQuestionMark, Copy, Save } from "lucide-react";

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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useNotify } from "@/components/ui/notification";
import { StartRunDialog } from "@/components/runs/start-run-dialog";
import { ApiError, createSupervisor, updateSupervisor } from "@/lib/api";
import { ACTION_LABEL } from "@/lib/display";
import type {
  ActionName,
  SupervisorDraft,
  SupervisorRecord,
} from "@/lib/contracts";

const ACTIONS: ActionName[] = [
  "message_fulfillment_team",
  "message_payments_team",
  "message_logistics_team",
  "message_customer",
  "create_internal_note",
];

// The backend revalidates all of this; these bounds exist so the operator is told
// before a save is refused rather than after.
const BOUNDS = {
  standard: { lower: 30, upper: 3600 },
  demo: { lower: 10, upper: 60 },
} as const;

const AGE_CHOICES = [
  { seconds: 1800, label: "30 minutes" },
  { seconds: 3600, label: "1 hour" },
  { seconds: 21600, label: "6 hours" },
  { seconds: 43200, label: "12 hours" },
  { seconds: 86400, label: "24 hours" },
  { seconds: 259200, label: "3 days" },
];

type Form = {
  name: string;
  instructions: string;
  actions: ActionName[];
  mode: "standard" | "demo";
  minimum: number;
  standard: number;
  maximum: number;
  maximumAge: number;
  customerReview: boolean;
  escalateDelays: boolean;
  prioritizeSpeed: boolean;
  modelLabel: string;
};

function fromRecord(record: SupervisorRecord): Form {
  const config = record.config;
  return {
    name: config.name,
    instructions: config.base_instructions,
    actions: [...config.allowed_actions],
    mode: config.wake_profile.mode,
    minimum: config.wake_profile.minimum_seconds,
    standard: config.wake_profile.default_seconds,
    maximum: config.wake_profile.maximum_seconds,
    maximumAge: config.maximum_age_seconds,
    customerReview: config.customer_review_default,
    escalateDelays: config.escalate_shipment_delays,
    prioritizeSpeed: config.prioritize_speed,
    modelLabel: config.model_label ?? "",
  };
}

function toDraft(form: Form): SupervisorDraft {
  return {
    name: form.name.trim(),
    base_instructions: form.instructions.trim(),
    allowed_actions: form.actions,
    wake_profile: {
      mode: form.mode,
      minimum_seconds: form.minimum,
      default_seconds: form.standard,
      maximum_seconds: form.maximum,
    },
    maximum_age_seconds: form.maximumAge,
    customer_review_default: form.customerReview,
    escalate_shipment_delays: form.escalateDelays,
    prioritize_speed: form.prioritizeSpeed,
    model_label: form.modelLabel.trim() || null,
  };
}

function problems(form: Form): Record<string, string> {
  const found: Record<string, string> = {};
  if (!form.name.trim()) found.name = "A configuration needs a name.";
  if (!form.instructions.trim()) {
    found.instructions = "Base instructions cannot be empty.";
  }
  if (form.actions.length === 0) {
    found.actions = "A supervisor with no allowed action can never act.";
  }
  const { lower, upper } = BOUNDS[form.mode];
  if (
    !(
      lower <= form.minimum &&
      form.minimum <= form.standard &&
      form.standard <= form.maximum &&
      form.maximum <= upper
    )
  ) {
    found.wake = `Review intervals must rise in order and stay between ${lower}s and ${upper}s.`;
  }
  if (form.mode === "demo" && form.maximumAge > 1800) {
    found.age = "Demo timing caps supervision at 30 minutes.";
  }
  return found;
}

function Interval({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (next: number) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

/**
 * Saving publishes a new version for future runs. A run already in progress keeps the
 * configuration it froze at creation, which is why this screen never claims to have
 * changed anything that is currently being supervised.
 */
export function SupervisorEditor({
  record,
  onSaved,
}: {
  record: SupervisorRecord;
  onSaved: (saved: SupervisorRecord) => void;
}) {
  const notify = useNotify();
  const [form, setForm] = useState<Form>(() => fromRecord(record));
  const [saving, setSaving] = useState<"none" | "update" | "create">("none");
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const invalid = useMemo(() => problems(form), [form]);
  const blocked = Object.keys(invalid).length > 0;

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setSaved(null);
  }

  function toggleAction(action: ActionName, enabled: boolean) {
    setForm((current) => ({
      ...current,
      actions: enabled
        ? [...current.actions, action]
        : current.actions.filter((item) => item !== action),
    }));
    setSaved(null);
  }

  function switchMode(mode: "standard" | "demo") {
    // Each profile has its own permitted range, so the intervals move with it.
    setForm((current) => ({
      ...current,
      mode,
      minimum: mode === "demo" ? 10 : 30,
      standard: mode === "demo" ? 20 : 300,
      maximum: mode === "demo" ? 60 : 3600,
      maximumAge:
        mode === "demo" ? Math.min(current.maximumAge, 1800) : current.maximumAge,
    }));
    setSaved(null);
  }

  async function submit(intent: "update" | "create") {
    if (blocked) return;
    setSaving(intent);
    setError(null);
    try {
      const draft = toDraft(form);
      const result =
        intent === "update"
          ? await updateSupervisor(record.config.id, {
              ...draft,
              expected_version: record.config.version,
            })
          : // A name the operator has already changed is the name they meant. Only an
            // untouched one needs disambiguating from the configuration it came from.
            await createSupervisor({
              ...draft,
              name:
                draft.name === record.config.name ? `${draft.name} (copy)` : draft.name,
            });
      setSaved(
        intent === "update"
          ? `Saved as version ${result.config.version}.`
          : `Created “${result.config.name}”.`,
      );
      notify({
        tone: "success",
        title:
          intent === "update"
            ? "Configuration saved"
            : "New configuration created",
        detail: "Runs already in progress keep the configuration they started with.",
      });
      onSaved(result);
    } catch (cause) {
      // The text stays exactly as typed; nothing is cleared on a refusal.
      setError(
        cause instanceof ApiError
          ? cause
          : new ApiError("The configuration could not be saved.", "network"),
      );
    } finally {
      setSaving("none");
    }
  }

  const busy = saving !== "none";

  return (
    <div className="panel">
      <div className="flex flex-col gap-3 border-b px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div>
          <h2 className="font-semibold tracking-tight">{record.config.name}</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            Version {record.config.version}
            {record.is_preset ? " · shipped preset" : ""}
          </p>
        </div>
        <StartRunDialog
          supervisorId={record.config.id}
          variant="outline"
          label="Start a run with this"
        />
      </div>

      <div className="space-y-7 px-5 py-6 sm:px-6">
        <section className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="config-name">Name</Label>
            <Input
              id="config-name"
              value={form.name}
              onChange={(event) => set("name", event.target.value)}
              aria-invalid={Boolean(invalid.name)}
              aria-describedby={invalid.name ? "config-name-problem" : undefined}
            />
            {invalid.name ? (
              <p id="config-name-problem" className="text-[13px] text-destructive">
                {invalid.name}
              </p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="config-instructions">Base instructions</Label>
            <Textarea
              id="config-instructions"
              value={form.instructions}
              onChange={(event) => set("instructions", event.target.value)}
              className="min-h-32"
              maxLength={4000}
              aria-invalid={Boolean(invalid.instructions)}
            />
            <p className="text-[13px] text-muted-foreground" data-numeric>
              {form.instructions.length} of 4000 characters. These reach every
              decision this supervisor makes.
            </p>
            {invalid.instructions ? (
              <p className="text-[13px] text-destructive">{invalid.instructions}</p>
            ) : null}
          </div>
        </section>

        <section className="space-y-3">
          <div>
            <h3 className="font-medium">Allowed actions</h3>
            <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
              Every action is a simulation recorded against the order. Nothing here
              sends a message to anyone.
            </p>
          </div>
          <div className="divide-y rounded-lg border">
            {ACTIONS.map((action) => (
              <div
                key={action}
                className="flex min-h-12 items-center justify-between gap-4 px-3.5 py-2.5"
              >
                <div className="flex items-center gap-2">
                  <Label htmlFor={`action-${action}`}>
                    {ACTION_LABEL[action]}
                  </Label>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`Tool name for ${ACTION_LABEL[action]}`}
                        className="rounded-full text-muted-foreground hover:text-foreground"
                      >
                        <CircleQuestionMark
                          className="size-4"
                          aria-hidden="true"
                        />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <code className="font-mono text-[12px]">{action}</code>
                    </TooltipContent>
                  </Tooltip>
                </div>
                <Switch
                  id={`action-${action}`}
                  checked={form.actions.includes(action)}
                  onCheckedChange={(checked) => toggleAction(action, checked)}
                />
              </div>
            ))}
          </div>
          {invalid.actions ? (
            <p className="text-[13px] text-destructive">{invalid.actions}</p>
          ) : null}
        </section>

        <section className="space-y-4">
          <div>
            <h3 className="font-medium">Wake behaviour</h3>
            <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
              How long the supervisor may wait before reviewing the order again. An
              important event still wakes it immediately.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="config-mode">Wake profile</Label>
            <Select
              value={form.mode}
              onValueChange={(value) =>
                switchMode(value as "standard" | "demo")
              }
            >
              <SelectTrigger id="config-mode" aria-label="Wake profile">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="standard">
                  Standard · 30s to 1 hour
                </SelectItem>
                <SelectItem value="demo">
                  Demo · 10s to 1 minute
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Interval
              id="config-minimum"
              label="Minimum (seconds)"
              value={form.minimum}
              onChange={(value) => set("minimum", value)}
            />
            <Interval
              id="config-default"
              label="Default (seconds)"
              value={form.standard}
              onChange={(value) => set("standard", value)}
            />
            <Interval
              id="config-maximum"
              label="Maximum (seconds)"
              value={form.maximum}
              onChange={(value) => set("maximum", value)}
            />
          </div>
          {invalid.wake ? (
            <p className="text-[13px] text-destructive">{invalid.wake}</p>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="config-age">Maximum supervision age</Label>
              <Select
                value={String(form.maximumAge)}
                onValueChange={(value) => set("maximumAge", Number(value))}
              >
                <SelectTrigger id="config-age" aria-label="Maximum supervision age">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AGE_CHOICES.filter(
                    (choice) => form.mode !== "demo" || choice.seconds <= 1800,
                  ).map((choice) => (
                    <SelectItem
                      key={choice.seconds}
                      value={String(choice.seconds)}
                    >
                      {choice.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[13px] leading-5 text-muted-foreground">
                The deadline is fixed when a run starts. It does not move on resume,
                retry, or an internal history rollover.
              </p>
            </div>
          </div>
          {invalid.age ? (
            <p className="text-[13px] text-destructive">{invalid.age}</p>
          ) : null}
        </section>

        <section className="space-y-3">
          <h3 className="font-medium">Operating policy</h3>
          <div className="divide-y rounded-lg border">
            <div className="flex min-h-14 items-center justify-between gap-4 px-3.5 py-3">
              <div>
                <Label htmlFor="config-review">
                  Customer messages need approval
                </Label>
                <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
                  A customer message is held as a draft until a person approves it.
                </p>
              </div>
              <Switch
                id="config-review"
                checked={form.customerReview}
                onCheckedChange={(checked) => set("customerReview", checked)}
              />
            </div>
            <div className="flex min-h-14 items-center justify-between gap-4 px-3.5 py-3">
              <div>
                <Label htmlFor="config-escalate">
                  Escalate shipment delays immediately
                </Label>
                <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
                  A recorded delay wakes the agent instead of waiting for the next
                  review.
                </p>
              </div>
              <Switch
                id="config-escalate"
                checked={form.escalateDelays}
                onCheckedChange={(checked) => set("escalateDelays", checked)}
              />
            </div>
            <div className="flex min-h-14 items-center justify-between gap-4 px-3.5 py-3">
              <div>
                <Label htmlFor="config-speed">Prioritise speed over cost</Label>
                <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
                  Review sooner while any concern is open. It does not authorise
                  contacting the same team repeatedly.
                </p>
              </div>
              <Switch
                id="config-speed"
                checked={form.prioritizeSpeed}
                onCheckedChange={(checked) => set("prioritizeSpeed", checked)}
              />
            </div>
          </div>
        </section>

        <details className="rounded-lg border px-3.5 py-3">
          <summary className="cursor-pointer font-medium select-none">
            Advanced
          </summary>
          <div className="mt-4 space-y-2">
            <Label htmlFor="config-model">Model label</Label>
            <Input
              id="config-model"
              value={form.modelLabel}
              onChange={(event) => set("modelLabel", event.target.value)}
              placeholder="e.g. gpt-oss-120b"
              autoComplete="off"
            />
            <p className="text-[13px] leading-5 text-muted-foreground">
              A readable name shown beside decisions. The provider and its key are
              backend configuration; no credential is ever collected in this browser.
            </p>
          </div>
        </details>
      </div>

      <div className="flex flex-col gap-3 border-t bg-muted/40 px-5 py-4 sm:px-6">
        {error ? (
          <div className="rounded-lg bg-alert-surface p-3 text-[13px] leading-5 text-destructive">
            <p>{error.message}</p>
            {Object.entries(error.fieldDetails).map(([field, detail]) => (
              <p key={field} className="mt-1">
                {field}: {detail}
              </p>
            ))}
          </div>
        ) : null}
        {saved ? (
          <div className="rounded-lg bg-quiet-surface p-3 text-[13px] leading-5 text-quiet">
            {saved} Existing runs keep their original configuration.
          </div>
        ) : null}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button
            variant="outline"
            className="h-10"
            onClick={() => submit("create")}
            disabled={blocked || busy}
          >
            <Copy className="size-4" aria-hidden="true" />
            {saving === "create" ? "Creating…" : "Save as new configuration"}
          </Button>
          <Button
            className="h-10"
            onClick={() => submit("update")}
            disabled={blocked || busy}
          >
            <Save className="size-4" aria-hidden="true" />
            {saving === "update" ? "Saving…" : "Save configuration"}
          </Button>
        </div>
      </div>
    </div>
  );
}
