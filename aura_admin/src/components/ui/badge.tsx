import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs leading-[1.6] font-medium",
  {
    variants: {
      tone: {
        neutral: "bg-surface-variant text-muted",
        ok: "bg-ok-soft text-ok",
        warn: "bg-warn-soft text-warn",
        bad: "bg-bad-soft text-bad",
        brand: "border border-brand-2/35 bg-brand-soft text-super",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type BadgeProps = HTMLAttributes<HTMLSpanElement> &
  VariantProps<typeof badgeVariants> & {
    /** A leading status dot; `pulse` adds a soft ring that radiates out of it. */
    dot?: boolean;
    pulse?: boolean;
  };

export function Badge({ className, tone, dot = false, pulse = false, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ tone }), dot && "ps-2", className)} {...props}>
      {dot ? (
        <span aria-hidden="true" className="relative inline-flex size-1.5">
          {pulse ? (
            <span className="absolute inset-0 rounded-full bg-current motion-safe:animate-pulse-dot" />
          ) : null}
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      ) : null}
      {children}
    </span>
  );
}
