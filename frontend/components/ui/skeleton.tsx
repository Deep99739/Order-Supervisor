import { cn } from "@/lib/utils";

/**
 * A placeholder shaped like the content that replaces it. It pulses once per second at
 * low contrast; it never re-enters when a poll refreshes data that is already on screen.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
