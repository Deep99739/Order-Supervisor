"use client";

import { useState } from "react";
import { CirclePause, CirclePlay, EllipsisVertical, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useNotify } from "@/components/ui/notification";
import { pauseRun, resumeRun, terminateRun } from "@/lib/api";
import { useCommand } from "@/lib/use-command";
import { isClosed, RECOVERY_ACTION } from "@/lib/display";
import type { RunSnapshot } from "@/lib/contracts";

/**
 * Pause, resume, terminate.
 *
 * Pressing a button never changes the displayed state: the header reads its label from
 * the run's own record, so "Paused" appears only once the workflow has confirmed that no
 * business action is still in flight.
 */
export function RunControls({
  runId,
  snapshot,
  onSubmitted,
}: {
  runId: string;
  snapshot: RunSnapshot;
  onSubmitted: () => void;
}) {
  const notify = useNotify();
  const [confirming, setConfirming] = useState(false);
  const hold = useCommand();
  const stop = useCommand();

  const closed = isClosed(snapshot.status);
  const paused = snapshot.status === "paused";
  const recovering = snapshot.status === "awaiting_recovery";
  const pending = snapshot.pending_control;

  async function send(kind: "pause" | "resume") {
    const sent = await hold.send((commandId) => {
      const body = { command_id: commandId, kind } as const;
      return kind === "pause"
        ? pauseRun(runId, body)
        : resumeRun(runId, body);
    });
    hold.restart();
    if (sent) {
      onSubmitted();
      notify({
        tone: "info",
        title: kind === "pause" ? "Pause requested" : "Resume requested",
        detail:
          kind === "pause"
            ? "An already-started action is allowed to finish first."
            : "The run reassesses the current context once it resumes.",
      });
    } else {
      notify({
        tone: "problem",
        title: "That command was not accepted",
        detail: hold.error?.message ?? "Try again.",
      });
    }
  }

  async function terminate() {
    const sent = await stop.send((commandId) =>
      terminateRun(runId, {
        command_id: commandId,
        kind: "terminate",
        reason: "Ended from the console",
      }),
    );
    stop.restart();
    setConfirming(false);
    if (sent) {
      onSubmitted();
      notify({
        tone: "info",
        title: "Termination requested",
        detail: "The run writes its final report before it closes.",
      });
    } else {
      notify({
        tone: "problem",
        title: "Termination was not accepted",
        detail: stop.error?.message ?? "Try again.",
      });
    }
  }

  if (closed) return null;

  const resumeLabel = recovering
    ? RECOVERY_ACTION[snapshot.recovery?.next_action ?? "retry_decision"]
    : "Resume";
  const busy = hold.status === "sending";

  return (
    <>
      {paused || recovering ? (
        <Button
          variant="outline"
          className="h-10"
          onClick={() => send("resume")}
          disabled={busy || pending === "resume"}
        >
          <CirclePlay className="size-4" aria-hidden="true" />
          {pending === "resume" ? "Resuming…" : resumeLabel}
        </Button>
      ) : (
        <Button
          variant="outline"
          className="h-10"
          onClick={() => send("pause")}
          disabled={busy || pending === "pause" || pending === "interrupt"}
        >
          <CirclePause className="size-4" aria-hidden="true" />
          {pending === "pause" || pending === "interrupt" ? "Pausing…" : "Pause"}
        </Button>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon-lg" aria-label="More run actions">
            <EllipsisVertical className="size-4" aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>Ending supervision</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={(event) => {
              event.preventDefault();
              setConfirming(true);
            }}
          >
            <Square className="size-4" aria-hidden="true" />
            Terminate this run
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogTitle>
            End supervision for {snapshot.order_id}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            The run writes a final report from what it actually recorded, then
            closes. Supervision cannot be resumed for this order afterwards, and no
            further events will be acted on.
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep supervising</AlertDialogCancel>
            <AlertDialogAction
              onClick={terminate}
              disabled={stop.status === "sending"}
            >
              {stop.status === "sending" ? "Ending…" : "End supervision"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
