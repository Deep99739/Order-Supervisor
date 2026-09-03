"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Play, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useNotify } from "@/components/ui/notification";
import { ApiError, createRun, getReadiness, listSupervisors } from "@/lib/api";
import { exampleOrderId, newId } from "@/lib/ids";
import type { JsonObject, RunCreated, SupervisorRecord } from "@/lib/contracts";

type Fields = {
  orderId: string;
  customer: string;
  description: string;
  payment: string;
  shipment: string;
  promisedAt: string;
};

const BLANK: Fields = {
  orderId: "",
  customer: "",
  description: "",
  payment: "",
  shipment: "",
  promisedAt: "",
};

const EXAMPLE: Omit<Fields, "orderId"> = {
  customer: "Sam Rivera",
  description: "1 × Studio headphones, express delivery",
  payment: "Authorised, awaiting capture",
  shipment: "Not dispatched yet",
  promisedAt: "",
};

function context(fields: Fields): JsonObject {
  const values: JsonObject = {};
  if (fields.customer.trim()) values.customer_display_name = fields.customer.trim();
  if (fields.description.trim()) values.description = fields.description.trim();
  if (fields.payment.trim()) values.payment_status = fields.payment.trim();
  if (fields.shipment.trim()) values.shipment_status = fields.shipment.trim();
  if (fields.promisedAt.trim()) {
    values.promised_shipment_at = fields.promisedAt.trim();
  }
  return values;
}

/**
 * Starting a run reserves an order identity. The `command_id` is minted once per attempt
 * and deliberately kept across retries: an unconfirmed start is retried against the same
 * reservation rather than becoming a second order.
 */
export function StartRunDialog({
  supervisorId,
  label = "Start run",
  variant = "default",
}: {
  supervisorId?: string;
  label?: string;
  variant?: "default" | "outline";
}) {
  const router = useRouter();
  const notify = useNotify();
  const [open, setOpen] = useState(false);
  const [supervisors, setSupervisors] = useState<SupervisorRecord[]>([]);
  const [supervisorsError, setSupervisorsError] = useState<string | null>(null);
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [chosen, setChosen] = useState(supervisorId ?? "");
  const [preset, setPreset] = useState<"none" | "short_review" | "short_expiry">(
    "none",
  );
  const [fields, setFields] = useState<Fields>(BLANK);
  const [commandId, setCommandId] = useState(() => newId());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [reserved, setReserved] = useState<RunCreated | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    listSupervisors(controller.signal)
      .then((list) => {
        setSupervisors(list.supervisors);
        setSupervisorsError(null);
        setChosen((current) =>
          current || (list.supervisors[0]?.config.id ?? ""),
        );
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setSupervisorsError(
          cause instanceof ApiError
            ? cause.message
            : "Supervisor configurations could not be read.",
        );
      });
    getReadiness(controller.signal)
      .then((readiness) => setDemoAvailable(readiness.demo_mode))
      .catch(() => setDemoAvailable(false));
    return () => controller.abort();
  }, [open]);

  const change = useCallback(
    (key: keyof Fields) => (value: string) =>
      setFields((current) => ({ ...current, [key]: value })),
    [],
  );

  const useExample = useCallback(() => {
    setFields({ ...EXAMPLE, orderId: exampleOrderId() });
    setError(null);
  }, []);

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      // Closing abandons this attempt, so the next one gets a fresh identity.
      setCommandId(newId());
      setReserved(null);
      setError(null);
      setSubmitting(false);
    }
  }

  async function start() {
    if (!chosen || !fields.orderId.trim()) return;
    const controller = new AbortController();
    request.current = controller;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createRun(
        {
          command_id: commandId,
          supervisor_id: chosen,
          order_id: fields.orderId.trim(),
          initial_context: context(fields),
          demo_timing_preset: preset === "none" ? null : preset,
        },
        controller.signal,
      );
      if (created.start === "started") {
        notify({
          tone: "success",
          title: `Supervision started for ${created.order_id}`,
          detail: "The first review happens on the worker, not in this browser.",
        });
        setOpen(false);
        setFields(BLANK);
        setCommandId(newId());
        setReserved(null);
        router.push(`/runs/${created.run_id}`);
      } else {
        // The order is reserved; only the workflow start is unconfirmed.
        setReserved(created);
      }
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(
        cause instanceof ApiError
          ? cause
          : new ApiError("The run could not be started.", "network"),
      );
    } finally {
      if (request.current === controller) request.current = null;
      setSubmitting(false);
    }
  }

  const ready = Boolean(chosen) && fields.orderId.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant={variant} className="h-10">
          <Play className="size-4" aria-hidden="true" />
          {label}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Start a supervised order</DialogTitle>
          <DialogDescription>
            These are synthetic order details. Nothing here looks up a real
            customer or contacts anyone.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-5">
          {supervisorsError ? (
            <p className="rounded-lg bg-alert-surface p-3 text-[13px] leading-5 text-destructive">
              {supervisorsError}
            </p>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="start-supervisor">Supervisor</Label>
            <Select value={chosen} onValueChange={setChosen}>
              <SelectTrigger id="start-supervisor" aria-label="Supervisor">
                <SelectValue placeholder="Choose a configuration" />
              </SelectTrigger>
              <SelectContent>
                {supervisors.map((record) => (
                  <SelectItem key={record.config.id} value={record.config.id}>
                    {record.config.name} · v{record.config.version}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[13px] leading-5 text-muted-foreground">
              The run freezes this configuration. Editing it later does not change
              a run already in progress.
            </p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="start-order">Order ID</Label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={useExample}
              >
                <Sparkles className="size-4" aria-hidden="true" />
                Use example order
              </Button>
            </div>
            <Input
              id="start-order"
              value={fields.orderId}
              onChange={(event) => change("orderId")(event.target.value)}
              placeholder="ORD-1042"
              autoComplete="off"
              required
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="start-customer">Customer name</Label>
              <Input
                id="start-customer"
                value={fields.customer}
                onChange={(event) => change("customer")(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="start-promised">Promised shipment time</Label>
              <Input
                id="start-promised"
                value={fields.promisedAt}
                onChange={(event) => change("promisedAt")(event.target.value)}
                placeholder="Optional, e.g. Friday 5pm"
                autoComplete="off"
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="start-description">Order description</Label>
            <Textarea
              id="start-description"
              value={fields.description}
              onChange={(event) => change("description")(event.target.value)}
              placeholder="What was ordered"
              className="min-h-16"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="start-payment">Payment so far</Label>
              <Input
                id="start-payment"
                value={fields.payment}
                onChange={(event) => change("payment")(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="start-shipment">Shipment so far</Label>
              <Input
                id="start-shipment"
                value={fields.shipment}
                onChange={(event) => change("shipment")(event.target.value)}
                autoComplete="off"
              />
            </div>
          </div>

          {demoAvailable ? (
            <div className="space-y-2">
              <Label htmlFor="start-timing">Review timing</Label>
              <Select
                value={preset}
                onValueChange={(value) =>
                  setPreset(value as "none" | "short_review" | "short_expiry")
                }
              >
                <SelectTrigger id="start-timing" aria-label="Review timing">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">
                    Template timing (minutes to hours)
                  </SelectItem>
                  <SelectItem value="short_review">
                    Demo · short reviews
                  </SelectItem>
                  <SelectItem value="short_expiry">
                    Demo · short maximum age
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[13px] leading-5 text-muted-foreground">
                Demo timing shortens this one run&rsquo;s waits. It changes no
                permission and no review rule.
              </p>
            </div>
          ) : null}

          {error ? (
            <div className="rounded-lg bg-alert-surface p-3 text-[13px] leading-5 text-destructive">
              <p>{error.message}</p>
              {error.runId ? (
                <Link
                  href={`/runs/${error.runId}`}
                  className="mt-1 inline-block font-medium underline underline-offset-2"
                >
                  Open the existing run
                </Link>
              ) : null}
            </div>
          ) : null}

          {reserved ? (
            <div className="rounded-lg bg-hold-surface p-3 text-[13px] leading-5 text-hold">
              <p className="font-medium">
                The order is reserved, but the supervisor start was not confirmed.
              </p>
              <p className="mt-1">
                {reserved.start_detail ??
                  "Retrying sends the same request against the same reserved run."}
              </p>
              <Link
                href={`/runs/${reserved.run_id}`}
                className="mt-1 inline-block font-medium underline underline-offset-2"
              >
                Open {reserved.order_id}
              </Link>
            </div>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            className="h-10"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button className="h-10" onClick={start} disabled={!ready || submitting}>
            {submitting
              ? "Starting…"
              : reserved
                ? "Retry start"
                : "Start supervision"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
