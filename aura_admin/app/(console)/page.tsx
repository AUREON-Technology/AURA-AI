"use client";

import { useEffect, useState } from "react";

import { GrafanaFrame } from "@/components/grafana-frame";
import { StatusPill } from "@/components/status-pill";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { AdminStatus } from "@/lib/types";

export default function OverviewPage() {
  const { t, errorText } = useI18n();
  const [status, setStatus] = useState<AdminStatus | null>(null);
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

  return (
    <>
      <h1>{t("nav.overview")}</h1>
      <p className="sub">{t("overview.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      {insecure ? (
        <div className="banner" data-testid="insecure-banner">
          {t("overview.insecure")}
        </div>
      ) : null}
      <div className="grid">
        <div className="card metric">
          {t("overview.ready")}
          <b>
            <StatusPill ok={Boolean(status?.health.healthy)} />
          </b>
        </div>
        <div className="card metric">
          {t("overview.environment")}
          <b>{status?.health.environment ?? "—"}</b>
        </div>
        <div className="card metric">
          {t("overview.activeIndex")}
          <b style={{ fontSize: 14 }}>{status?.health.index ?? "—"}</b>
        </div>
        <div className="card metric">
          {t("overview.learningRecords")}
          <b>{String(status?.learning.total_records ?? "—")}</b>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>{t("nav.providers")}</h2>
        <div className="row">
          {(status?.providers ?? []).map((provider) => (
            <StatusPill
              key={provider.provider}
              ok={provider.healthy && provider.state !== "open"}
              label={`${provider.provider} ${provider.state}`}
            />
          ))}
        </div>
      </div>
      <GrafanaFrame title={t("nav.overview")} uid="aura-overview" />
      <GrafanaFrame title={t("overview.grafanaQuality")} uid="aura-quality" />
      <GrafanaFrame title={t("overview.grafanaSecurity")} uid="aura-security" />
    </>
  );
}
