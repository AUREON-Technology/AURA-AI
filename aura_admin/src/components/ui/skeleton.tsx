import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** A placeholder block with a shimmer sweep (static under prefers-reduced-motion). */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "block h-4 rounded-sm bg-surface-variant",
        "bg-[linear-gradient(90deg,transparent_0%,var(--shimmer)_50%,transparent_100%)] bg-size-[200%_100%] bg-no-repeat",
        "motion-safe:animate-shimmer",
        className,
      )}
      {...props}
    />
  );
}
