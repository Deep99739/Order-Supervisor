"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Bot,
  Clock,
  Filter,
  FileText,
  Flag,
  Inbox,
  Layers,
  PackagePlus,
  RefreshCcw,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StateBadge } from "@/components/state-badge";
import { EmptyState } from "@/components/states";
import { accentOf, categoryOf, groupRecords, recordTitle } from "@/lib/activity";
import { ACTION_AUDIENCE } from "@/lib/contracts";
import {
  AUDIENCE_LABEL,
  BLOCK_REASON,
  clockTime,
  DISPOSITION_LABEL,
  DISPOSITION_TONE,
  HINT_LABEL,
  NOTE_CATEGORY_LABEL,
  TONE_CLASS,
  TONE_DOT,
  TONE_EDGE,
  type Tone,
  TRIGGER_LABEL,
} from "@/lib/display";
import { cn } from "@/lib/utils";
import type {
  ActionAudience,
  ActionName,
  ActivityKind,
  ActivityRecord,
  BlockReason,
  DecisionTrigger,
  JsonObject,
  NoteCategory,
  WakeHint,
} from "@/lib/contracts";

const ICONS: Record<ActivityKind, LucideIcon> = {
  run_reserved: PackagePlus,
  event: Inbox,
  policy: Filter,
  decision: Bot,
  action: Send,
  instruction: FileText,
  control: SlidersHorizontal,
  review: ShieldCheck,
  sleep: Clock,
  memory: Layers,
  continuation: RefreshCcw,
  recovery: TriangleAlert,
  finalization: Flag,
  operation_receipt: RefreshCcw,
};

const FILTERS = [
  { value: "all", label: "Everything" },
  { value: "events", label: "Events" },
  { value: "actions", label: "Agent" },
  { value: "system", label: "System" },
] as const;

type FeedFilter = (typeof FILTERS)[number]["value"];

// The key for the row edges. Deliberately five entries: any more and it stops being
// readable at a glance, which is the only reason the colours are there.
const LEGEND: { label: string; tone: Tone }[] = [
  { label: "Order event", tone: "working" },
  { label: "Agent", tone: "quiet" },
  { label: "Recorded action", tone: "done" },
  { label: "Refused or held", tone: "hold" },
  { label: "System", tone: "stopped" },
];

function str(details: JsonObject, key: string): string | null {
  const value = details[key];
  return typeof value === "string" ? value : null;
}

function num(details: JsonObject, key: string): number | null {
  const value = details[key];
  return typeof value === "number" ? value : null;
}

function Quote({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-2 rounded-lg border-l-2 border-primary/40 bg-muted/60 px-3 py-2 leading-6 whitespace-pre-wrap">
      {children}
    </p>
  );
}

function Meta({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-1.5 text-[13px] leading-5 text-muted-foreground">
      {children}
    </p>
  );
}

/** Payloads, identifiers, and provider diagnostics stay out of the default reading flow. */
function Details({ record }: { record: ActivityRecord }) {
  const payload = {
    sequence: record.sequence,
    recorded_at: record.recorded_at,
    occurred_at: record.occurred_at,
    command_id: record.command_id,
    event_id: record.event_id,
    decision_id: record.decision_id,
    action_id: record.action_id,
    details: record.details,
  };
  return (
    <details className="group mt-2">
      <summary className="w-fit cursor-pointer text-[13px] text-muted-foreground select-none hover:text-foreground">
        Details
      </summary>
      <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-[#0f172a] p-3 font-mono text-[12px] leading-5 text-[#e2e8f0]">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </details>
  );
}

function ActionBody({ record }: { record: ActivityRecord }) {
  const details = record.details;
  const action = str(details, "action");
  // A blocked proposal carries no audience of its own — it never reached the registry
  // that supplies one — so the registry's mapping fills it in for display only.
  const named = str(details, "audience");
  const audience =
    named ??
    (action && action in ACTION_AUDIENCE
      ? ACTION_AUDIENCE[action as ActionName]
      : null);
  const subject = str(details, "subject");
  const category = str(details, "category");
  const content = str(details, "content");
  const reason = str(details, "reason");
  const issue = str(details, "issue_id");
  const committed = record.disposition === "committed";

  return (
    <>
      <Meta>
        {audience
          ? `${committed ? "Recorded for" : "Proposed to"} ${AUDIENCE_LABEL[
              audience as ActionAudience
            ].toLowerCase()}`
          : committed
            ? "Recorded action"
            : "Proposed action"}
        {category && category in NOTE_CATEGORY_LABEL
          ? ` · ${NOTE_CATEGORY_LABEL[category as NoteCategory]}`
          : ""}
        {issue ? ` · about “${issue}”` : ""}
        {committed ? " · simulated" : ""}
      </Meta>
      {subject ? <p className="mt-2 font-medium">{subject}</p> : null}
      {content ? <Quote>{content}</Quote> : null}
      {!committed && reason ? (
        <Meta>
          {reason in BLOCK_REASON
            ? BLOCK_REASON[reason as BlockReason]
            : reason}
          . Nothing was recorded for this proposal.
        </Meta>
      ) : null}
    </>
  );
}

function DecisionBody({ record }: { record: ActivityRecord }) {
  const details = record.details;
  const trigger = str(details, "trigger");
  const stage = str(details, "stage");
  const provenance = str(details, "provenance");
  const model = str(details, "model_label");
  const attempts = num(details, "attempts");
  const admitted = num(details, "admitted");
  const blocked = num(details, "blocked");
  const recommendation = str(details, "completion_recommendation");

  return (
    <>
      <Meta>
        {trigger && trigger in TRIGGER_LABEL
          ? TRIGGER_LABEL[trigger as DecisionTrigger]
          : "Review"}
        {provenance
          ? ` · ${provenance === "scripted" ? "scripted stand-in, not a model" : "model"}`
          : ""}
        {model ? ` · ${model}` : ""}
        {attempts !== null && attempts > 1 ? ` · ${attempts} attempts` : ""}
      </Meta>
      {stage === "completed" && (admitted !== null || blocked !== null) ? (
        <Meta>
          {admitted ?? 0} action{admitted === 1 ? "" : "s"} recorded ·{" "}
          {blocked ?? 0} blocked
        </Meta>
      ) : null}
      {recommendation ? (
        <Meta>
          The agent suggested closing: “{recommendation}”. Only the workflow can
          close a run.
        </Meta>
      ) : null}
    </>
  );
}

function Body({ record }: { record: ActivityRecord }) {
  const details = record.details;
  switch (record.kind) {
    case "action":
      return <ActionBody record={record} />;
    case "decision":
      return <DecisionBody record={record} />;
    case "policy": {
      const hint = str(details, "guidance_hint");
      const version = num(details, "guidance_version");
      if (!hint) return null;
      return (
        <Meta>
          Followed the agent&rsquo;s own guidance:{" "}
          {hint in HINT_LABEL ? HINT_LABEL[hint as WakeHint["kind"]] : hint}
          {version !== null ? ` (v${version})` : ""}.
        </Meta>
      );
    }
    case "instruction": {
      const text = str(details, "text");
      return text ? <Quote>{text}</Quote> : null;
    }
    case "sleep": {
      const next = str(details, "next_wake_at");
      const template = details.used_template_default === true;
      if (!next) return null;
      return (
        <Meta>
          Next review at{" "}
          <time dateTime={next}>{new Date(next).toLocaleString()}</time>
          {template ? " · the template's default interval" : ""}
        </Meta>
      );
    }
    case "memory": {
      const from = num(details, "covered_from");
      const through = num(details, "covered_through");
      const before = num(details, "before_chars");
      const after = num(details, "after_chars");
      const provenance = str(details, "provenance");
      return (
        <Meta>
          Covers records {from ?? "?"}–{through ?? "?"}
          {before !== null && after !== null
            ? ` · ${before} to ${after} characters`
            : ""}
          {provenance === "model"
            ? " · written by the agent"
            : " · rendered from the record"}
          . Instructions and open concerns are not compacted.
        </Meta>
      );
    }
    case "recovery": {
      const reason = str(details, "reason");
      return reason ? <Meta>{reason}</Meta> : null;
    }
    case "finalization": {
      const provenance = str(details, "provenance");
      if (!provenance) return null;
      return (
        <Meta>
          {provenance === "model"
            ? "The closing narrative was written by the agent."
            : "The closing narrative was rendered from the record."}
        </Meta>
      );
    }
    default:
      return null;
  }
}

function Entry({ record }: { record: ActivityRecord }) {
  const Icon = ICONS[record.kind];
  const accent = accentOf(record);
  return (
    // The left edge is the only thing on this row that is purely visual. It makes a long
    // timeline scannable by category without asking anyone to trust the colour alone.
    <li
      className={cn(
        "flex gap-3 border-l-[3px] py-3 pl-3 first:rounded-tl last:rounded-bl",
        TONE_EDGE[accent],
      )}
    >
      <div className="flex w-14 shrink-0 flex-col items-end pt-0.5">
        <time
          className="text-[13px] text-muted-foreground"
          dateTime={record.recorded_at}
        >
          {clockTime(record.recorded_at)}
        </time>
      </div>
      <div
        aria-hidden="true"
        className={cn(
          "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg",
          TONE_CLASS[accent],
        )}
      >
        <Icon className="size-[15px]" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="font-medium">{recordTitle(record)}</span>
          <StateBadge
            label={DISPOSITION_LABEL[record.disposition]}
            tone={DISPOSITION_TONE[record.disposition]}
            dot={false}
            className="px-2 py-0.5 text-[12px]"
          />
        </div>
        <p className="mt-1 leading-6">{record.explanation}</p>
        <Body record={record} />
        <Details record={record} />
      </div>
    </li>
  );
}

export function ActivityFeed({
  records,
  loading,
  earlierCursor,
  loadingEarlier,
  onLoadEarlier,
}: {
  records: ActivityRecord[];
  loading: boolean;
  earlierCursor: number | null;
  loadingEarlier: boolean;
  onLoadEarlier: () => void;
}) {
  const [filter, setFilter] = useState<FeedFilter>("all");
  const [accepted, setAccepted] = useState<number | null>(null);
  const top = useRef<HTMLDivElement>(null);
  const newest = useRef(0);

  const latest = records.at(-1)?.sequence ?? 0;
  useEffect(() => {
    newest.current = latest;
  }, [latest]);

  // While the operator is reading further down, newly recorded rows are held back
  // instead of pushing the page under their eyes.
  useEffect(() => {
    const node = top.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setAccepted(null);
        else setAccepted((current) => current ?? newest.current);
      },
      { threshold: 0 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const cutoff = accepted ?? Number.MAX_SAFE_INTEGER;
  const selected = records.filter(
    (record) => filter === "all" || categoryOf(record) === filter,
  );
  const visible = selected.filter((record) => record.sequence <= cutoff);
  const waiting = selected.length - visible.length;
  const groups = groupRecords(visible).reverse();

  function reveal() {
    setAccepted(null);
    top.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={filter === option.value}
              onClick={() => setFilter(option.value)}
              className={cn(
                "min-h-9 rounded-full border px-3 text-[13px] font-medium transition-colors duration-150",
                filter === option.value
                  ? "border-primary/30 bg-accent text-accent-foreground"
                  : "border-transparent bg-muted text-muted-foreground hover:text-foreground",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        {/* Without this the edge colours are decoration. With it they are a key. */}
        <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted-foreground">
          {LEGEND.map((item) => (
            <li key={item.label} className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className={cn("h-3.5 w-[3px] rounded-full", TONE_DOT[item.tone])}
              />
              {item.label}
            </li>
          ))}
        </ul>
      </div>

      <div ref={top} />

      {waiting > 0 ? (
        <div className="sticky top-3 z-20 mb-3 flex justify-center">
          <Button size="sm" className="shadow-overlay" onClick={reveal}>
            <ArrowUp className="size-4" aria-hidden="true" />
            {waiting} new update{waiting === 1 ? "" : "s"}
          </Button>
        </div>
      ) : null}

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-16 w-full" />
          ))}
        </div>
      ) : null}

      {!loading && groups.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="Nothing recorded in this view"
          description={
            filter === "all"
              ? "This run has no activity yet."
              : "No record of this kind has been written yet. Try another filter."
          }
        />
      ) : null}

      <ol className="space-y-3">
        {groups.map((group) => {
          const grouped = group.records.length > 1;
          return (
            <li
              key={group.key}
              className={cn(
                grouped ? "rounded-xl border bg-muted/25 px-4 py-1.5" : "px-1",
              )}
            >
              {/* One review and everything it wrote, kept together without hiding a
                  single receipt. */}
              <ul className={cn(grouped && "divide-y")}>
                {group.records.map((record) => (
                  <Entry key={record.id} record={record} />
                ))}
              </ul>
            </li>
          );
        })}
      </ol>

      {earlierCursor !== null ? (
        <div className="mt-5 flex justify-center">
          <Button
            variant="outline"
            onClick={onLoadEarlier}
            disabled={loadingEarlier}
          >
            {loadingEarlier ? "Loading…" : "Load earlier activity"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
