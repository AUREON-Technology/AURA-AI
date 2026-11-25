"use client";

import { useCallback, useEffect, useState } from "react";

import { PlatformCard } from "@/components/platform-card";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { ControlOverview } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

export default function SettingsOverview() {
  const { t, errorText } = useI18n();
  const [overview, setOverview] = useState<ControlOverview | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api<ControlOverview>("/admin/control/overview")
      .then((body) => {
        setOverview(body);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  if (error && !overview) {
    return <Alert>{errorText(error)}</Alert>;
  }
  if (!overview) {
    return <Skeleton className="h-40 w-full" />;
  }
  const revision = String(overview.release.revision ?? overview.release.git_sha ?? "?");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
        <span data-testid="settings-release">{t("settings.release", { revision, environment: overview.environment })}</span>
        <Badge tone={overview.open_changes ? "warn" : "neutral"}>{t("settings.openChanges", { count: overview.open_changes })}</Badge>
      </div>
      {!overview.vault_ready ? <Alert data-testid="vault-missing">{t("settings.vaultMissing")}</Alert> : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {overview.platforms.map((row) => (
          <PlatformCard key={row.platform} row={row} vaultReady={overview.vault_ready} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
