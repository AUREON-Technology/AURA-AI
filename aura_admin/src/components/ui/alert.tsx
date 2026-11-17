import type { HTMLAttributes } from "react";
import { TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/** An error or warning strip, e.g. a failed request or the insecure-mode notice. */
export function Alert({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="alert"
      className={cn(
        "mb-4 flex items-start gap-2.5 rounded-sm border border-bad/40 border-s-[3px] border-s-bad bg-bad-soft px-3.5 py-2.5 text-[13.5px] text-fg",
        "motion-safe:animate-fade-up",
        className,
      )}
      {...props}
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-bad" />
      <div className="min-w-0 break-words">{children}</div>
    </div>
  );
}
