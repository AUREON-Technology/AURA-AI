import type { ReactNode } from "react";

/** The page's one h1 plus its lede; every console page starts with this. */
export function PageHeader({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="mb-1.5 text-2xl leading-tight font-bold tracking-[-0.02em]">{title}</h1>
        {children ? <p className="max-w-[70ch] text-muted">{children}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
