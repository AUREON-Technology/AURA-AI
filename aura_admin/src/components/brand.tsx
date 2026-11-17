"use client";

import { useI18n } from "@/lib/i18n/context";
import { cn } from "@/lib/utils";

function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-[10px] bg-linear-135 from-brand to-brand-2 text-[15px] font-extrabold text-white shadow-glow",
        className,
      )}
    >
      A
    </span>
  );
}

export function Brand({ title, className }: { title: string; className?: string }) {
  const { t } = useI18n();
  return (
    <div className={cn("flex items-center gap-2.5 font-bold", className)}>
      <BrandMark />
      <span className="flex flex-col leading-tight">
        <span>{title}</span>
        <small className="text-[11.5px] font-medium text-muted">{t("brand.workspace")}</small>
      </span>
    </div>
  );
}
