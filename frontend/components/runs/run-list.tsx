"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Inbox, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StateBadge } from "@/components/state-badge";
import { EmptyState, ErrorState, StaleNotice } from "@/components/states";
import { StartRunDialog } from "@/components/runs/start-run-dialog";
import { useRuns } from "@/lib/use-runs";
import { useServerNow } from "@/lib/clock";
import {
  CLOSE_REASON,
  contextLine,
  isClosed,
  progressSummary,
  relativeTime,
  supervisorState,
  untilTime,
} from "@/lib/display";
import { cn } from "@/lib/utils";
import type { RunListItem } from "@/lib/contracts";

const FILTERS = [
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
] as const;

type Filter = (typeof FILTERS)[number]["value"];

function NextReview({ run, now }: { run: RunListItem; now: number }) {
  if (isClosed(run.status)) {
    return (
      <span className="text-muted-foreground">
        {run.close_reason ? CLOSE_REASON[run.close_reason] : "Closed"}
      </span>
    );
  }
  if (!run.next_wake_at) {
    return <span className="text-muted-foreground">No review scheduled</span>;
  }
  return (
    <span>
      <time dateTime={run.next_wake_at}>
        {new Date(run.next_wake_at).toLocaleTimeString()}
      </time>
      <span className="ml-1.5 text-muted-foreground">
        {untilTime(run.next_wake_at, now)}
      </span>
    </span>
  );
}

function Row({ run, now }: { run: RunListItem; now: number }) {
  const state = supervisorState(run);
  const context = contextLine(run.initial_context);
  return (
    <TableRow className="relative">
      <TableCell className="max-w-[22rem]">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Link
              href={`/runs/${run.run_id}`}
              className="font-medium tracking-tight after:absolute after:inset-0 hover:text-primary"
            >
              {run.order_id}
            </Link>
            {context ? (
              <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
                {context}
              </p>
            ) : null}
          </div>
          {/* The state column is hidden on a narrow screen, so it travels here rather
              than scrolling out of sight. */}
          <div className="sm:hidden">
            <StateBadge label={state.label} tone={state.tone} />
          </div>
        </div>
        <p className="mt-1.5 text-[13px] text-muted-foreground md:hidden">
          {progressSummary(run.facts)} · <NextReview run={run} now={now} />
        </p>
      </TableCell>
      <TableCell className="hidden text-muted-foreground lg:table-cell">
        {run.supervisor_name}
      </TableCell>
      <TableCell className="hidden md:table-cell">
        {progressSummary(run.facts)}
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        <StateBadge label={state.label} tone={state.tone} />
      </TableCell>
      <TableCell className="hidden text-[13px] md:table-cell">
        <NextReview run={run} now={now} />
      </TableCell>
      <TableCell className="hidden text-[13px] whitespace-nowrap text-muted-foreground sm:table-cell">
        <time dateTime={run.updated_at}>{relativeTime(run.updated_at, now)}</time>
      </TableCell>
    </TableRow>
  );
}

export function RunList() {
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setApplied(search), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const { page, loading, error, refreshing, refresh } = useRuns(filter, applied);
  const now = useServerNow(page?.observed_at);

  const summary = useMemo(() => {
    if (!page) return null;
    const open = page.runs.filter((run) => !isClosed(run.status)).length;
    return `${page.runs.length} shown · ${open} active`;
  }, [page]);

  const showTable = page !== null && page.runs.length > 0;
  const showEmpty = page !== null && page.runs.length === 0 && !loading;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-[26px] leading-8 font-semibold tracking-tight">
            Runs
          </h1>
          <p className="mt-1.5 text-muted-foreground">
            One durable supervisor per order, from creation to closure.
          </p>
        </div>
        <StartRunDialog />
      </div>

      <div className="panel overflow-hidden">
        <div className="flex flex-col gap-3 border-b p-3 sm:flex-row sm:items-center sm:justify-between sm:px-4">
          <div
            role="group"
            aria-label="Filter runs by state"
            className="flex w-fit items-center gap-1 rounded-lg bg-muted p-1"
          >
            {FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={filter === option.value}
                onClick={() => setFilter(option.value)}
                className={cn(
                  "min-h-9 rounded-md px-3 text-[13px] font-medium transition-colors duration-150",
                  filter === option.value
                    ? "bg-card text-foreground shadow-panel"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <div className="relative flex-1 sm:w-64 sm:flex-none">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search order ID"
                aria-label="Search by order ID"
                className="pl-9"
              />
            </div>
            <Button
              variant="outline"
              size="icon-lg"
              onClick={refresh}
              disabled={refreshing}
              aria-label="Refresh the run list"
            >
              <RefreshCw className="size-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        {error && page ? (
          <StaleNotice
            observedAt={page.observed_at}
            message="The run list could not be refreshed."
            onRetry={refresh}
          />
        ) : null}

        {loading ? (
          <div className="space-y-3 p-4">
            {[0, 1, 2].map((row) => (
              <Skeleton key={row} className="h-12 w-full" />
            ))}
          </div>
        ) : null}

        {!loading && error && !page ? (
          <ErrorState
            title="The run list could not be read"
            description={error.message}
            onRetry={refresh}
            retrying={refreshing}
          />
        ) : null}

        {!loading && showTable ? (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Order</TableHead>
                <TableHead className="hidden lg:table-cell">Template</TableHead>
                <TableHead className="hidden md:table-cell">
                  Order progress
                </TableHead>
                <TableHead className="hidden sm:table-cell">
                  Supervisor state
                </TableHead>
                <TableHead className="hidden md:table-cell">
                  Next review
                </TableHead>
                <TableHead className="hidden sm:table-cell">
                  Last update
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.runs.map((run) => (
                <Row key={run.run_id} run={run} now={now} />
              ))}
            </TableBody>
          </Table>
        ) : null}

        {!loading && showEmpty && !error ? (
          applied ? (
            <EmptyState
              icon={Search}
              title="No order matches that search"
              description={`Nothing recorded here has an order ID containing “${applied}”.`}
              action={
                <Button variant="outline" onClick={() => setSearch("")}>
                  Clear the search
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={Inbox}
              title="No runs yet"
              description="Start a run to put one order under supervision. Its events, decisions, and recorded actions all appear here."
              action={<StartRunDialog />}
            />
          )
        ) : null}

        {showTable && summary ? (
          <div className="flex items-center justify-between gap-4 border-t bg-muted/35 px-4 py-3 text-[13px] text-muted-foreground">
            <span data-numeric>{summary}</span>
            {page.next_cursor ? (
              <span>More runs exist beyond this page.</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
