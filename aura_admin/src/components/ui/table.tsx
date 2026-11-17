import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Real <table> markup (the e2e suite selects rows and cells by role). The wrapper carries the
 * surface and scrolls sideways on narrow screens; `flush` drops the surface inside a Card.
 */
export function Table({
  className,
  flush = false,
  ...props
}: HTMLAttributes<HTMLTableElement> & { flush?: boolean }) {
  return (
    <div
      className={cn(
        "w-full overflow-x-auto",
        flush ? "-mx-5 w-[calc(100%+2.5rem)] border-y border-line" : "rounded-md border border-line bg-surface shadow-card",
      )}
    >
      <table className={cn("w-full border-separate border-spacing-0 text-left text-sm", className)} {...props} />
    </div>
  );
}

export function TableHeader(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead {...props} />;
}

export function TableBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody {...props} />;
}

export function TableRow({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn(
        "transition-colors duration-150 [tbody>&:hover]:bg-surface-variant/60 [tbody>&:last-child>td]:border-b-0",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-line bg-surface-variant px-3.5 py-2.5 text-[11.5px] font-semibold tracking-[0.06em] whitespace-nowrap text-muted uppercase",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn("border-b border-line px-3.5 py-2.5 align-middle tabular-nums", className)}
      {...props}
    />
  );
}

/** One full-width row for "nothing here yet". */
export function TableEmpty({ colSpan, children }: { colSpan: number; children: React.ReactNode }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-3.5 py-8 text-center text-[13px] text-faint">
        {children}
      </td>
    </tr>
  );
}

/** Shimmering placeholder rows while the first load is in flight. */
export function TableSkeleton({ cols, rows = 3 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, row) => (
        <tr key={row} aria-hidden="true">
          {Array.from({ length: cols }, (_, col) => (
            <td key={col} className="border-b border-line px-3.5 py-3">
              <Skeleton className={col === 0 ? "w-32" : col === cols - 1 ? "ml-auto w-20" : "w-16"} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
