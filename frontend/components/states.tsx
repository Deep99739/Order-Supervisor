import type { ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Nothing has been recorded yet — which is different from a read that failed. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-6 py-14 text-center",
        className,
      )}
    >
      <div className="mb-5 flex size-14 items-center justify-center rounded-2xl border border-primary/15 bg-accent text-primary">
        <Icon className="size-6" strokeWidth={1.6} aria-hidden="true" />
      </div>
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      <p className="mt-2 max-w-[430px] leading-6 text-muted-foreground">
        {description}
      </p>
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}

/** A read failed. This never says "no data"; it says what could not be read. */
export function ErrorState({
  title,
  description,
  onRetry,
  retrying,
  className,
}: {
  title: string;
  description: string;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-6 py-14 text-center",
        className,
      )}
    >
      <div className="mb-5 flex size-14 items-center justify-center rounded-2xl border border-destructive/15 bg-alert-surface text-destructive">
        <TriangleAlert className="size-6" strokeWidth={1.6} aria-hidden="true" />
      </div>
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      <p className="mt-2 max-w-[430px] leading-6 text-muted-foreground">
        {description}
      </p>
      {onRetry ? (
        <Button
          variant="outline"
          className="mt-6 h-11"
          onClick={onRetry}
          disabled={retrying}
        >
          {retrying ? "Trying again…" : "Try again"}
        </Button>
      ) : null}
    </div>
  );
}

/** A read failed while earlier data is still on screen. */
export function StaleNotice({
  observedAt,
  message,
  onRetry,
}: {
  observedAt: string | null;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex flex-col gap-2 border-b bg-hold-surface px-4 py-3 text-[13px] leading-5 text-hold sm:flex-row sm:items-center sm:justify-between">
      <p>
        <span className="font-medium">{message}</span>{" "}
        {observedAt ? (
          <>
            Showing what was recorded at{" "}
            <time dateTime={observedAt}>
              {new Date(observedAt).toLocaleTimeString()}
            </time>
            .
          </>
        ) : null}
      </p>
      <Button
        variant="outline"
        size="sm"
        className="w-fit bg-card"
        onClick={onRetry}
      >
        Retry
      </Button>
    </div>
  );
}
