"use client";

import { useI18n } from "@/lib/i18n/context";

export function StatusPill({ ok, label }: { ok: boolean; label?: string }) {
  const { t } = useI18n();
  return <span className={`pill ${ok ? "ok" : "bad"}`}>{label ?? (ok ? t("status.healthy") : t("status.down"))}</span>;
}
