import { cn } from "@/lib/utils";
import { TONE_CLASS, TONE_DOT, type Tone } from "@/lib/display";

/**
 * Colour never carries the meaning on its own: every badge shows its label, and the dot
 * is decorative reinforcement for operators scanning a long list.
 */
export function StateBadge({
  label,
  tone,
  className,
  dot = true,
}: {
  label: string;
  tone: Tone;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex w-fit shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1",
        "text-[13px] leading-4 font-medium whitespace-nowrap",
        TONE_CLASS[tone],
        className,
      )}
    >
      {dot ? (
        <span
          aria-hidden="true"
          className={cn("size-1.5 rounded-full", TONE_DOT[tone])}
        />
      ) : null}
      {label}
    </span>
  );
}
