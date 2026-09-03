"use client";

import Link from "next/link";
import { ChevronRight, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StateBadge } from "@/components/state-badge";
import { ErrorState, StaleNotice } from "@/components/states";
import { ActivityFeed } from "@/components/runs/activity-feed";
import { DraftReviewCard } from "@/components/runs/draft-review";
import { EventSheet } from "@/components/runs/event-sheet";
import { InstructionControls } from "@/components/runs/instruction-controls";
import { InstructionDialog } from "@/components/runs/instruction-dialog";
import { MemoryTab } from "@/components/runs/memory-tab";
import { OutcomeTab } from "@/components/runs/outcome-tab";
import { RunControls } from "@/components/runs/run-controls";
import {
  FactsCard,
  InstructionListCard,
  NextReviewCard,
  RecoveryCard,
} from "@/components/runs/side-panels";
import { useRun } from "@/lib/use-run";
import { useServerNow } from "@/lib/clock";
import {
  isClosed,
  progressSummary,
  progressTone,
  relativeTime,
  supervisorState,
} from "@/lib/display";

function Loading() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-28 w-full" />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Skeleton className="h-96 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    </div>
  );
}

export function RunDetail({ runId }: { runId: string }) {
  const feed = useRun(runId);
  const view = feed.view;
  const now = useServerNow(view?.observed_at);

  if (feed.loading && !view) return <Loading />;

  if (!view) {
    return (
      <div className="panel">
        <ErrorState
          title="This run could not be read"
          description={
            feed.error?.message ??
            "The console could not load this run from the API."
          }
          onRetry={feed.refresh}
          retrying={feed.refreshing}
        />
      </div>
    );
  }

  const snapshot = view.snapshot;
  const state = supervisorState(snapshot);
  const closed = isClosed(snapshot.status);
  // A closed run records nothing further. Events and instructions stay visible but
  // disabled, with the reason on the control itself.
  const closedReason = closed
    ? "Supervision has closed for this order; it accepts no further commands."
    : "";

  const urgent = (
    <>
      <RecoveryCard snapshot={snapshot} />
      <DraftReviewCard
        runId={runId}
        snapshot={snapshot}
        onSubmitted={feed.refresh}
      />
      <NextReviewCard snapshot={snapshot} now={now} />
    </>
  );

  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-[13px]">
        <Link
          href="/runs"
          className="rounded text-muted-foreground hover:text-foreground"
        >
          Runs
        </Link>
        <ChevronRight
          className="size-3.5 text-muted-foreground"
          aria-hidden="true"
        />
        <span className="font-medium">{snapshot.order_id}</span>
      </nav>

      <header className="panel overflow-hidden">
        {feed.error ? (
          <StaleNotice
            observedAt={view.observed_at}
            message="This run could not be refreshed."
            onRetry={feed.refresh}
          />
        ) : null}
        <div className="flex flex-col gap-4 px-5 py-4 sm:px-6">
          <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
            <div className="min-w-0">
              <h1 className="text-[22px] leading-7 font-semibold tracking-tight">
                {snapshot.order_id}
              </h1>
              <p className="mt-1 text-[13px] text-muted-foreground">
                {snapshot.supervisor.name} · started{" "}
                <time dateTime={snapshot.started_at}>
                  {new Date(snapshot.started_at).toLocaleString()}
                </time>
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <EventSheet
                runId={runId}
                records={feed.records}
                disabled={closed}
                disabledReason={closedReason}
                onSubmitted={feed.refresh}
              />
              <InstructionDialog
                runId={runId}
                snapshot={snapshot}
                disabled={closed}
                disabledReason={closedReason}
                onSubmitted={feed.refresh}
              />
              <RunControls
                runId={runId}
                snapshot={snapshot}
                onSubmitted={feed.refresh}
              />
              <Button
                variant="outline"
                size="icon-lg"
                onClick={feed.refresh}
                disabled={feed.refreshing}
                aria-label="Refresh this run"
              >
                <RefreshCw className="size-4" aria-hidden="true" />
              </Button>
            </div>
          </div>

          {closed ? (
            <p className="-mt-1 text-[13px] text-muted-foreground">
              {closedReason}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t pt-4">
            <StateBadge
              label={progressSummary(snapshot.facts)}
              tone={progressTone(snapshot.facts)}
            />
            <StateBadge label={state.label} tone={state.tone} />
            <span className="text-[13px] text-muted-foreground">
              {state.hint}
            </span>
            <span className="ml-auto text-[13px] text-muted-foreground">
              Last recorded{" "}
              <time dateTime={snapshot.updated_at}>
                {relativeTime(snapshot.updated_at, now)}
              </time>
            </span>
          </div>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start">
        {/* On a narrow screen the state that needs a person comes before the timeline
            rather than below it. */}
        <div className="space-y-5 lg:hidden">{urgent}</div>

        <main className="panel min-w-0 px-4 py-4 sm:px-5">
          <Tabs defaultValue="activity">
            <TabsList className="mb-5 border-b">
              <TabsTrigger value="activity" className="mr-4">
                Activity
              </TabsTrigger>
              <TabsTrigger value="memory" className="mr-4">
                Memory
              </TabsTrigger>
              <TabsTrigger value="outcome">Outcome</TabsTrigger>
            </TabsList>
            <TabsContent value="activity">
              <ActivityFeed
                records={feed.records}
                loading={feed.loading}
                earlierCursor={feed.earlierCursor}
                loadingEarlier={feed.loadingEarlier}
                onLoadEarlier={feed.loadEarlier}
              />
            </TabsContent>
            <TabsContent value="memory">
              <MemoryTab snapshot={snapshot} />
            </TabsContent>
            <TabsContent value="outcome">
              <OutcomeTab snapshot={snapshot} />
            </TabsContent>
          </Tabs>
        </main>

        <aside className="space-y-5 lg:sticky lg:top-5 lg:max-h-[calc(100dvh-2.5rem)] lg:overflow-y-auto lg:pb-1">
          <div className="hidden space-y-5 lg:block">{urgent}</div>
          <FactsCard snapshot={snapshot} now={now} />
          <InstructionListCard
            snapshot={snapshot}
            renderControls={(instructionId) => {
              const instruction = snapshot.instructions.find(
                (item) => item.instruction_id === instructionId,
              );
              return instruction ? (
                <InstructionControls
                  runId={runId}
                  instruction={instruction}
                  disabled={closed}
                  onSubmitted={feed.refresh}
                />
              ) : null;
            }}
          />
        </aside>
      </div>
    </div>
  );
}
