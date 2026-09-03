"use client";

import { useCallback, useMemo, useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/states";
import { StateBadge } from "@/components/state-badge";
import { SupervisorEditor } from "@/components/supervisors/supervisor-editor";
import { ApiError, listSupervisors } from "@/lib/api";
import { useOnce } from "@/lib/polling";
import { durationLabel } from "@/lib/display";
import { cn } from "@/lib/utils";
import type { SupervisorConfig, SupervisorRecord } from "@/lib/contracts";

/** A plain sentence about what this configuration actually does differently. */
function behaviour(config: SupervisorConfig): string {
  const parts = [
    `${config.allowed_actions.length} of 5 actions`,
    `reviews about every ${durationLabel(config.wake_profile.default_seconds)}`,
    config.customer_review_default
      ? "customer messages need approval"
      : "customer messages go out unreviewed",
  ];
  if (config.escalate_shipment_delays) parts.push("delays escalate immediately");
  return parts.join(" · ");
}

export function SupervisorWorkbench() {
  const [records, setRecords] = useState<SupervisorRecord[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [reloading, setReloading] = useState(false);

  const load = useCallback(async () => {
    setReloading(true);
    try {
      const list = await listSupervisors();
      setRecords(list.supervisors);
      setSelected((current) => current ?? list.supervisors[0]?.config.id ?? null);
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause
          : new ApiError("Configurations could not be read.", "network"),
      );
    } finally {
      setReloading(false);
    }
  }, []);

  useOnce(load);

  const onSaved = useCallback(
    (saved: SupervisorRecord) => {
      setRecords((current) => {
        if (!current) return [saved];
        const known = current.some(
          (record) => record.config.id === saved.config.id,
        );
        return known
          ? current.map((record) =>
              record.config.id === saved.config.id ? saved : record,
            )
          : [...current, saved];
      });
      setSelected(saved.config.id);
    },
    [],
  );

  const active = useMemo(
    () => records?.find((record) => record.config.id === selected) ?? null,
    [records, selected],
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[26px] leading-8 font-semibold tracking-tight">
          Supervisors
        </h1>
        <p className="mt-1.5 max-w-2xl text-muted-foreground">
          Instructions, allowed actions, and review timing. A run freezes the
          configuration it starts with, so editing one never rewrites an order
          already being supervised.
        </p>
      </div>

      {error && !records ? (
        <div className="panel">
          <ErrorState
            title="Configurations could not be read"
            description={error.message}
            onRetry={() => void load()}
            retrying={reloading}
          />
        </div>
      ) : null}

      {!records && !error ? (
        <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-[32rem] w-full" />
        </div>
      ) : null}

      {records ? (
        <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)] lg:items-start">
          <nav aria-label="Supervisor configurations" className="space-y-2">
            {records.map((record) => {
              const chosen = record.config.id === selected;
              return (
                <button
                  key={record.config.id}
                  type="button"
                  aria-current={chosen ? "true" : undefined}
                  onClick={() => setSelected(record.config.id)}
                  className={cn(
                    "w-full rounded-xl border p-4 text-left transition-colors duration-150",
                    chosen
                      ? "border-primary/40 bg-accent"
                      : "bg-card hover:border-input hover:bg-muted/50",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-medium tracking-tight">
                      {record.config.name}
                    </span>
                    {record.is_preset ? (
                      <StateBadge label="Preset" tone="stopped" dot={false} />
                    ) : null}
                  </div>
                  <p className="mt-1.5 text-[13px] leading-5 text-muted-foreground">
                    {behaviour(record.config)}
                  </p>
                </button>
              );
            })}
            {records.length === 0 ? (
              <div className="panel px-4 py-6 text-center text-[13px] leading-5 text-muted-foreground">
                <SlidersHorizontal
                  className="mx-auto mb-3 size-5 text-primary"
                  aria-hidden="true"
                />
                No configurations exist yet. Run the backend&rsquo;s migration
                command to seed the three presets.
              </div>
            ) : null}
          </nav>

          {active ? (
            <SupervisorEditor
              // Remounting on a saved version keeps the form aligned with what the
              // API returned; a failed save leaves the typed text untouched.
              key={`${active.config.id}:${active.config.version}`}
              record={active}
              onSaved={onSaved}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
