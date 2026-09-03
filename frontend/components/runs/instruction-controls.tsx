"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useNotify } from "@/components/ui/notification";
import { submitInstruction } from "@/lib/api";
import { useCommand } from "@/lib/use-command";
import { INSTRUCTION_LIMIT } from "@/lib/display";
import type { ActiveInstruction } from "@/lib/contracts";

/**
 * Replacing or removing an instruction is explicit and recorded. The old text stays in
 * the run's history either way — this changes what the supervisor is operating under
 * now, it does not rewrite what it was operating under before.
 */
export function InstructionControls({
  runId,
  instruction,
  disabled,
  onSubmitted,
}: {
  runId: string;
  instruction: ActiveInstruction;
  disabled: boolean;
  onSubmitted: () => void;
}) {
  const notify = useNotify();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(instruction.text);
  const replace = useCommand();
  const remove = useCommand();

  const tooLong = text.length > INSTRUCTION_LIMIT;

  async function submitReplacement() {
    if (!text.trim() || tooLong) return;
    const sent = await replace.send((commandId) =>
      submitInstruction(runId, {
        command_id: commandId,
        operation: "supersede",
        instruction_id: instruction.instruction_id,
        text: text.trim(),
        // The named controls are not re-stated here, so this replacement leaves the
        // customer-contact question unanswered unless it already was answered.
        policy_changes: instruction.policy_changes,
      }),
    );
    if (sent) {
      onSubmitted();
      setOpen(false);
      replace.restart();
      notify({
        tone: "info",
        title: "Replacement accepted",
        detail: "The previous instruction stays in this run's history.",
      });
    }
  }

  async function submitRemoval() {
    const sent = await remove.send((commandId) =>
      submitInstruction(runId, {
        command_id: commandId,
        operation: "remove",
        instruction_id: instruction.instruction_id,
      }),
    );
    if (sent) {
      onSubmitted();
      remove.restart();
      notify({
        tone: "info",
        title: "Removal accepted",
        detail: "The instruction no longer applies from the next review onward.",
      });
    }
  }

  return (
    <span className="flex items-center gap-1">
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) replace.restart();
        }}
      >
        <DialogTrigger asChild>
          <Button variant="ghost" size="sm" disabled={disabled}>
            Replace
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Replace this instruction</DialogTitle>
            <DialogDescription>
              The supervisor follows the new text from its next review. The original
              stays in the run&rsquo;s history.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-2">
            <Label htmlFor={`replace-${instruction.instruction_id}`}>
              Instruction
            </Label>
            <Textarea
              id={`replace-${instruction.instruction_id}`}
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="min-h-32"
            />
            <p className="text-[13px] text-muted-foreground" data-numeric>
              {text.length} of {INSTRUCTION_LIMIT} characters.
            </p>
            {replace.error ? (
              <p className="rounded-lg bg-alert-surface p-3 text-[13px] leading-5 text-destructive">
                {replace.error.message}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              className="h-10"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              className="h-10"
              onClick={submitReplacement}
              disabled={
                !text.trim() || tooLong || replace.status === "sending"
              }
            >
              {replace.status === "sending" ? "Sending…" : "Replace"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled || remove.status === "sending"}
            className="text-muted-foreground hover:text-destructive"
          >
            Remove
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogTitle>Remove this instruction?</AlertDialogTitle>
          <AlertDialogDescription>
            The supervisor stops following it from its next review. If it was
            holding customer messages for approval, that hold may lift. The
            instruction and this removal both stay in the run&rsquo;s history.
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={submitRemoval}>
              Remove instruction
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </span>
  );
}
