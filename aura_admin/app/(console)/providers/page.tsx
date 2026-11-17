"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusPill } from "@/components/status-pill";
import { api } from "@/lib/api";
import type { AdminStatus, ProviderRow, ResetCircuitResult } from "@/lib/types";

export default function ProvidersPage() {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<{ scope?: string; pid?: number }>({});
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => {
        setRows(status.providers ?? []);
        setScope({ scope: status.providers_scope, pid: status.worker_pid });
      })
      .catch((err: Error) => setError(err.message));
  }

  useEffect(load, []);

  return (
    <>
      <h1>Providers</h1>
      <p className="sub">Circuit state from the kernel router.</p>
      {/* Breakers live in each gateway worker's memory: this table and a reset reach only the
          worker that answered, so say so rather than imply a deployment-wide view. */}
      {scope.scope === "worker" ? (
        <p className="muted" data-testid="providers-scope">
          Per-worker state: this view and any reset apply to gateway worker
          {scope.pid !== undefined ? ` ${scope.pid}` : ""} only; other workers keep their own breakers.
        </p>
      ) : null}
      {error ? <div className="banner">{error}</div> : null}
      {notice ? (
        <p className="muted" data-testid="reset-notice">
          {notice}
        </p>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>Provider</th>
            <th>State</th>
            <th>Failure rate</th>
            <th>Latency</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.provider}>
              <td>{row.provider}</td>
              <td>
                <StatusPill ok={row.state !== "open"} label={row.state} />
              </td>
              <td>{row.failure_rate}</td>
              <td>{row.latency_ms ?? "—"}</td>
              <td>
                <ConfirmDialog
                  title={`Reset ${row.provider}`}
                  confirmText={row.provider}
                  onConfirm={async () => {
                    const result = await api<ResetCircuitResult>(
                      `/admin/providers/${encodeURIComponent(row.provider)}/reset-circuit`,
                      { method: "POST" },
                    );
                    const where =
                      result?.scope === "worker"
                        ? ` on worker${result.worker_pid !== undefined ? ` ${result.worker_pid}` : ""} only`
                        : "";
                    setNotice(`Reset ${row.provider}${where}.${result?.note ? ` ${result.note}` : ""}`);
                    load();
                  }}
                >
                  Reset circuit
                </ConfirmDialog>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
