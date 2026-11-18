"use client";

import { useEffect, useState } from "react";

import { Activity, Database, Globe, HeartPulse } from "lucide-react";

import { GrafanaFrame } from "@/components/grafana-frame";
import { StatusPill } from "@/components/status-pill";
import { Alert } from "@/components/ui/alert";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric, MetricGrid } from "@/components/ui/metric";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { AdminStatus } from "@/lib/types";

export default function OverviewPage() {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const { t, errorText } = useI18n();
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api<AdminStatus>("/admin/status")
      .then(setStatus)
      .catch((err: unknown) => setError(err));
  }, []);

  const insecure = Boolean(
    status && JSON.stringify(status.settings).includes("allow_mock_auth") &&
      JSON.stringify(status.settings).match(/"allow_mock_auth"\s*:\s*true/),
  );

  const loading = !status && !error;

  return (
    <>
      <PageHeader title={t("nav.overview")}>{t("overview.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      {insecure ? (
        <Alert data-testid="insecure-banner">
          {t("overview.insecure")}
        </Alert>
      ) : null}
      <MetricGrid>
        <Metric
          index={0}
          icon={HeartPulse}
          label={t("overview.ready")}
          loading={loading}
          value={<StatusPill ok={Boolean(status?.health.healthy)} />}
        />
        <Metric index={1} icon={Globe} label={t("overview.environment")} loading={loading} value={status?.health.environment ?? "—"} />
        <Metric
          index={2}
          icon={Database}
          label={t("overview.activeIndex")}
          compact
          loading={loading}
          value={status?.health.index ?? "—"}
        />
        <Metric
          index={3}
          icon={Activity}
          label={t("overview.learningRecords")}
          loading={loading}
          value={String(status?.learning.total_records ?? "—")}
        />
      </MetricGrid>
      <Card className="mt-4 motion-safe:animate-fade-up [animation-delay:280ms]">
        <CardHeader className="mb-3">
          <div>
            <CardTitle>{t("nav.providers")}</CardTitle>
            <CardDescription>{t("overview.providersHelp")}</CardDescription>
          </div>
        </CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          {loading ? (
            <>
              <Skeleton className="h-6 w-28 rounded-full" />
              <Skeleton className="h-6 w-32 rounded-full" />
            </>
          ) : null}
          {(status?.providers ?? []).map((provider) => (
            <StatusPill
              key={provider.provider}
              ok={provider.healthy && provider.state !== "open"}
              label={`${provider.provider} ${provider.state}`}
            />
          ))}
        </div>
      </Card>
      <GrafanaFrame title={t("nav.overview")} uid="aura-overview" />
      <GrafanaFrame title={t("overview.grafanaQuality")} uid="aura-quality" />
      <GrafanaFrame title={t("overview.grafanaSecurity")} uid="aura-security" />
    </>
  );
}
