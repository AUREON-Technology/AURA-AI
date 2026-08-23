"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusPill } from "@/components/status-pill";
import { api } from "@/lib/api";
import type { AdminStatus, ProviderRow } from "@/lib/types";

export default function ProvidersPage() {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => setRows(status.providers ?? []))
      .catch((err: Error) => setError(err.message));
  }

  useEffect(load, []);

  return (
    <>
      <h1>Providers</h1>
      <p className="sub">Circuit state from the kernel router.</p>
      {error ? <div className="banner">{error}</div> : null}
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
                    await api(`/admin/providers/${row.provider}/reset-circuit`, { method: "POST" });
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
