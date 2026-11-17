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
import type { AdminStatus } from "@/lib/types";

export default function OverviewPage() {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AdminStatus>("/admin/status")
      .then(setStatus)
      .catch((err: Error) => setError(err.message));
  }, []);

  const insecure = Boolean(
    status && JSON.stringify(status.settings).includes("allow_mock_auth") &&
      JSON.stringify(status.settings).match(/"allow_mock_auth"\s*:\s*true/),
  );

  const loading = !status && !error;

  return (
    <>
      <PageHeader title="Overview">Gateway health, last refresh, and Grafana.</PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      {insecure ? (
        <Alert data-testid="insecure-banner">
          Insecure mode is active. Production must show aura_insecure_mode = 0.
        </Alert>
      ) : null}
      <MetricGrid>
        <Metric
          index={0}
          icon={HeartPulse}
          label="Ready"
          loading={loading}
          value={<StatusPill ok={Boolean(status?.health.healthy)} />}
        />
        <Metric index={1} icon={Globe} label="Environment" loading={loading} value={status?.health.environment ?? "—"} />
        <Metric
          index={2}
          icon={Database}
          label="Active index"
          compact
          loading={loading}
          value={status?.health.index ?? "—"}
        />
        <Metric
          index={3}
          icon={Activity}
          label="Learning records"
          loading={loading}
          value={String(status?.learning.total_records ?? "—")}
        />
      </MetricGrid>
      <Card className="mt-4 motion-safe:animate-fade-up [animation-delay:280ms]">
        <CardHeader className="mb-3">
          <div>
            <CardTitle>Providers</CardTitle>
            <CardDescription>Circuit state per provider, as the router sees it.</CardDescription>
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
      <GrafanaFrame title="Overview" uid="aura-overview" />
      <GrafanaFrame title="AI Quality" uid="aura-quality" />
      <GrafanaFrame title="Security" uid="aura-security" />
    </>
  );
}
