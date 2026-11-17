"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusPill } from "@/components/status-pill";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { AdminStatus, ProviderRow, ResetCircuitResult } from "@/lib/types";

export default function ProvidersPage() {
  const { t, errorText } = useI18n();
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [scope, setScope] = useState<{ scope?: string; pid?: number }>({});
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => {
        setRows(status.providers ?? []);
        setScope({ scope: status.providers_scope, pid: status.worker_pid });
      })
      .catch((err: unknown) => setError(err));
  }

  useEffect(load, []);

  return (
    <>
      <h1>{t("nav.providers")}</h1>
      <p className="sub">{t("providers.sub")}</p>
      {/* Breakers live in each gateway worker's memory: this table and a reset reach only the
          worker that answered, so say so rather than imply a deployment-wide view. */}
      {scope.scope === "worker" ? (
        <p className="muted" data-testid="providers-scope">
          {scope.pid !== undefined
            ? t("providers.scopeWorker", { pid: scope.pid })
            : t("providers.scopeWorkerUnknown")}
        </p>
      ) : null}
      {error ? <div className="banner">{errorText(error)}</div> : null}
      {notice ? (
        <p className="muted" data-testid="reset-notice">
          {notice}
        </p>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>{t("providers.colProvider")}</th>
            <th>{t("providers.colState")}</th>
            <th>{t("providers.colFailureRate")}</th>
            <th>{t("providers.colLatency")}</th>
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
                  title={t("providers.resetTitle", { provider: row.provider })}
                  confirmText={row.provider}
                  onConfirm={async () => {
                    const result = await api<ResetCircuitResult>(
                      `/admin/providers/${encodeURIComponent(row.provider)}/reset-circuit`,
                      { method: "POST" },
                    );
                    const done =
                      result?.scope !== "worker"
                        ? t("providers.resetDone", { provider: row.provider })
                        : result.worker_pid !== undefined
                          ? t("providers.resetDoneWorker", { provider: row.provider, pid: result.worker_pid })
                          : t("providers.resetDoneWorkerUnknown", { provider: row.provider });
                    // The gateway's own note is server text and stays as sent.
                    setNotice(`${done}${result?.note ? ` ${result.note}` : ""}`);
                    load();
                  }}
                >
                  {t("providers.resetCircuit")}
                </ConfirmDialog>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
