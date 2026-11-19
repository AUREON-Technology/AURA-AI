"use client";

import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/lib/i18n/context";

/** Healthy pulses; anything else holds still so the eye goes to it. */
export function StatusPill({ ok, label }: { ok: boolean; label?: string }) {
  const { t } = useI18n();
  return (
    <Badge tone={ok ? "ok" : "bad"} dot pulse={ok}>
      {label ?? (ok ? t("status.healthy") : t("status.down"))}
    </Badge>
  );
}
