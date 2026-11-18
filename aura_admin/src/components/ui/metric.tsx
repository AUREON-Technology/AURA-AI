import type { ComponentType, ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * A KPI tile. `index` staggers its entrance after its siblings; `loading` swaps the value for a
 * shimmer placeholder.
 */
export function Metric({
  label,
  value,
  icon: Icon,
  index = 0,
  loading = false,
  compact = false,
}: {
  label: string;
  value: ReactNode;
  icon?: ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" }>;
  index?: number;
  loading?: boolean;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-md border border-line bg-surface p-4 shadow-card desk:p-5",
        "transition-[border-color,transform] duration-200 hover:border-line-strong motion-safe:hover:-translate-y-0.5",
        "motion-safe:animate-fade-up",
      )}
      style={{ animationDelay: `${index * 70}ms` }}
    >
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-0.5 bg-linear-90 from-brand to-brand-2 opacity-70"
      />
      <div className="flex items-center justify-between gap-2 text-xs font-medium tracking-[0.02em] text-muted">
        {label}
        {Icon ? <Icon aria-hidden="true" className="size-4 text-faint transition-colors group-hover:text-accent" /> : null}
      </div>
      <div
        className={cn(
          "mt-2 font-bold tracking-[-0.02em] text-fg tabular-nums [overflow-wrap:anywhere]",
          compact ? "text-sm" : "text-xl desk:text-2xl",
        )}
      >
        {loading ? <Skeleton className={cn("mt-1", compact ? "h-4 w-40" : "h-7 w-24")} /> : value}
      </div>
    </div>
  );
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-2.5 desk:grid-cols-[repeat(auto-fit,minmax(200px,1fr))] desk:gap-3.5">{children}</div>;
}
