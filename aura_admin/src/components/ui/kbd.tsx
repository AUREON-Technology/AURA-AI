import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 items-center rounded-[5px] border border-line-strong bg-surface-variant px-1.5 font-sans text-[11px] text-muted",
        className,
      )}
      {...props}
    />
  );
}
