"use client";

import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleHelp,
  RefreshCw,
  Server,
  TriangleAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, getReadiness } from "@/lib/api";
import type { Readiness } from "@/lib/setup-contracts";

function Dependency({ name, available }: { name: string; available: boolean }) {
  const Icon = available ? CheckCircle2 : TriangleAlert;
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <dt className="text-muted-foreground">{name}</dt>
      <dd
        className={`flex items-center gap-2 font-medium ${available ? "text-primary" : "text-destructive"}`}
      >
        <Icon className="size-4" aria-hidden="true" />
        {available ? "Connected" : "Unavailable"}
      </dd>
    </div>
  );
}

export function ServiceCheck() {
  const [result, setResult] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => () => activeRequest.current?.abort(), []);

  async function check() {
    if (activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setChecking(true);
    setError(null);
    try {
      const data = await getReadiness(controller.signal);
      if (!controller.signal.aborted) setResult(data);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof ApiError
            ? cause.message
            : "The service check could not finish. Try again.",
        );
      }
    } finally {
      if (!controller.signal.aborted) setChecking(false);
      if (activeRequest.current === controller) activeRequest.current = null;
    }
  }

  const status = error
    ? "Check failed"
    : result?.status === "ready"
      ? "Services connected"
      : result
        ? "Setup needed"
        : "Not checked";
  const modelLabel =
    result?.model === "scripted"
      ? "Scripted mode"
      : result?.model === "configured_not_tested"
        ? "Configured · untested"
        : "Not configured";

  return (
    <Card className="gap-0 overflow-hidden py-0 shadow-none">
      <CardContent className="p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 rounded-lg bg-muted p-2 text-muted-foreground">
              <Server className="size-4" aria-hidden="true" />
            </span>
            <div>
              <h2 className="font-semibold">Local service check</h2>
              <p className="mt-1 text-muted-foreground">
                Check the connections behind this workspace.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3 pl-11 sm:pl-0">
            <Badge variant="secondary" className="rounded-md font-medium">
              {status}
            </Badge>
            <Button
              variant="outline"
              className="h-11"
              onClick={check}
              disabled={checking}
            >
              <RefreshCw className="size-4" aria-hidden="true" />
              {checking
                ? "Checking…"
                : result || error
                  ? "Check again"
                  : "Check services"}
            </Button>
          </div>
        </div>

        <div role="status" aria-live="polite" aria-atomic="true">
          {checking ? (
            <p className="mt-5 text-muted-foreground">
              Checking the API, database, and workflow service…
            </p>
          ) : null}
          {error ? (
            <p className="mt-5 rounded-lg bg-red-50 p-3 text-destructive">
              {error}
            </p>
          ) : null}
        </div>

        {result ? (
          <div className="mt-5 border-t pt-4">
            {error || checking ? (
              <p className="mb-3 text-muted-foreground">
                Last successful check — these results may be out of date.
              </p>
            ) : null}
            <dl className="grid gap-x-8 sm:grid-cols-2 xl:grid-cols-3">
              <Dependency name="API" available />
              <Dependency
                name="PostgreSQL"
                available={result.database === "available"}
              />
              <Dependency
                name="Temporal"
                available={result.temporal === "available"}
              />
              <div className="flex items-center justify-between gap-4 py-2.5">
                <dt className="text-muted-foreground">Worker</dt>
                <dd className="flex items-center gap-2">
                  <CircleHelp
                    className="size-4 text-muted-foreground"
                    aria-hidden="true"
                  />
                  Not checked
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4 py-2.5">
                <dt className="text-muted-foreground">Model</dt>
                <dd className="font-medium">{modelLabel}</dd>
              </div>
              <div className="flex items-center justify-between gap-4 py-2.5">
                <dt className="text-muted-foreground">Timing</dt>
                <dd>{result.demo_mode ? "Demo" : "Standard"}</dd>
              </div>
            </dl>
            <p className="mt-4 text-[13px] leading-5 text-muted-foreground">
              Checked{" "}
              <time dateTime={result.checked_at}>
                {new Date(result.checked_at).toLocaleString()}
              </time>
              . This check does not verify worker polling or agent decisions.
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
