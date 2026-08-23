"use client";

import { useEffect, useState } from "react";

import { GrafanaFrame } from "@/components/grafana-frame";
import { StatusPill } from "@/components/status-pill";
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

  return (
    <>
      <h1>Overview</h1>
      <p className="sub">Gateway health, last refresh, and Grafana.</p>
      {error ? <div className="banner">{error}</div> : null}
      {insecure ? (
        <div className="banner" data-testid="insecure-banner">
          Insecure mode is active. Production must show aura_insecure_mode = 0.
        </div>
      ) : null}
      <div className="grid">
        <div className="card metric">
          Ready
          <b>
            <StatusPill ok={Boolean(status?.health.healthy)} />
          </b>
        </div>
        <div className="card metric">
          Environment
          <b>{status?.health.environment ?? "—"}</b>
        </div>
        <div className="card metric">
          Active index
          <b style={{ fontSize: 14 }}>{status?.health.index ?? "—"}</b>
        </div>
        <div className="card metric">
          Learning records
          <b>{String(status?.learning.total_records ?? "—")}</b>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Providers</h2>
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
      <GrafanaFrame title="Overview" uid="aura-overview" />
      <GrafanaFrame title="AI Quality" uid="aura-quality" />
      <GrafanaFrame title="Security" uid="aura-security" />
    </>
  );
}
