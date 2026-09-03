"use client";

import { useCallback, useState } from "react";

import { ApiError } from "./api";
import { newId } from "./ids";

export type CommandStatus = "idle" | "sending" | "accepted" | "failed";

/**
 * One logical operator command and its identity.
 *
 * The `commandId` survives a failure on purpose: retrying the same submission must reuse
 * it, so the backend resolves the retry to the original outcome instead of applying a
 * second one. Only an explicit new submission calls `restart`.
 *
 * `accepted` means the API took the command and processing is pending. It never means
 * the command was applied — that is what the run's own receipt says.
 */
export function useCommand() {
  const [commandId, setCommandId] = useState(() => newId());
  const [status, setStatus] = useState<CommandStatus>("idle");
  const [error, setError] = useState<ApiError | null>(null);

  const send = useCallback(
    async (submit: (id: string) => Promise<unknown>): Promise<boolean> => {
      setStatus("sending");
      setError(null);
      try {
        await submit(commandId);
        setStatus("accepted");
        return true;
      } catch (cause) {
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError("The command could not be sent.", "network"),
        );
        setStatus("failed");
        return false;
      }
    },
    [commandId],
  );

  const restart = useCallback(() => {
    setCommandId(newId());
    setStatus("idle");
    setError(null);
  }, []);

  return { commandId, status, error, send, restart };
}
