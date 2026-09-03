"use client";

import * as React from "react";
import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";

import { cn } from "@/lib/utils";

type Tone = "info" | "success" | "problem";

type Notice = { id: number; tone: Tone; title: string; detail?: string };

const NotifyContext = React.createContext<
  ((notice: Omit<Notice, "id">) => void) | null
>(null);

/**
 * Feedback for a command that has been accepted, not a claim that it has been applied.
 * The run's own recorded state is what says an event landed or an action was taken.
 */
export function useNotify() {
  const notify = React.useContext(NotifyContext);
  if (!notify) throw new Error("useNotify must be used inside <Notifications>");
  return notify;
}

const ICONS: Record<Tone, typeof Info> = {
  info: Info,
  success: CheckCircle2,
  problem: TriangleAlert,
};

const TONES: Record<Tone, string> = {
  info: "text-working",
  success: "text-quiet",
  problem: "text-destructive",
};

export function Notifications({ children }: { children: React.ReactNode }) {
  const [notices, setNotices] = React.useState<Notice[]>([]);
  const counter = React.useRef(0);

  const dismiss = React.useCallback((id: number) => {
    setNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const notify = React.useCallback((notice: Omit<Notice, "id">) => {
    const id = ++counter.current;
    setNotices((current) => [...current.slice(-2), { ...notice, id }]);
    window.setTimeout(() => dismiss(id), 6000);
  }, [dismiss]);

  return (
    <NotifyContext.Provider value={notify}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed inset-x-3 bottom-3 z-[60] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-5 sm:bottom-5"
      >
        {notices.map((notice) => {
          const Icon = ICONS[notice.tone];
          return (
            <div
              key={notice.id}
              className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border bg-card p-3.5 shadow-overlay"
            >
              <Icon
                className={cn("mt-0.5 size-[18px] shrink-0", TONES[notice.tone])}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className="font-medium">{notice.title}</p>
                {notice.detail ? (
                  <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
                    {notice.detail}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => dismiss(notice.id)}
                aria-label="Dismiss notification"
                className="rounded-md p-1 text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </NotifyContext.Provider>
  );
}
