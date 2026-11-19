"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusPill } from "@/components/status-pill";
import { Alert } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { useI18n } from "@/lib/i18n/context";
import { api } from "@/lib/api";
import type { AdminStatus, ProviderRow, ResetCircuitResult } from "@/lib/types";

export default function ProvidersPage() {
  const { t, errorText } = useI18n();
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loaded, setLoaded] = useState(false);
  const [scope, setScope] = useState<{ scope?: string; pid?: number }>({});
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => {
        setRows(status.providers ?? []);
        setScope({ scope: status.providers_scope, pid: status.worker_pid });
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoaded(true));
  }

  useEffect(load, []);

  return (
    <>
      <PageHeader title={t("nav.providers")}>{t("providers.sub")}</PageHeader>
      {/* Breakers live in each gateway worker's memory: this table and a reset reach only the
          worker that answered, so say so rather than imply a deployment-wide view. */}
      {scope.scope === "worker" ? (
        <p className="mb-4 text-[13px] text-muted" data-testid="providers-scope">
          {scope.pid !== undefined
            ? t("providers.scopeWorker", { pid: scope.pid })
            : t("providers.scopeWorkerUnknown")}
        </p>
      ) : null}
      {error ? <Alert>{errorText(error)}</Alert> : null}
      {notice ? (
        <p className="mb-4 text-[13px] text-muted motion-safe:animate-fade-up" data-testid="reset-notice">
          {notice}
        </p>
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("providers.colProvider")}</TableHead>
            <TableHead>{t("providers.colState")}</TableHead>
            <TableHead>{t("providers.colFailureRate")}</TableHead>
            <TableHead>{t("providers.colLatency")}</TableHead>
            <TableHead className="w-0"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.provider}>
              <TableCell className="font-medium">{row.provider}</TableCell>
              <TableCell>
                <StatusPill ok={row.state !== "open"} label={row.state} />
              </TableCell>
              <TableCell>{row.failure_rate}</TableCell>
              <TableCell>{row.latency_ms ?? "—"}</TableCell>
              <TableCell className="text-end">
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
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={5} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={5}>{t("providers.empty")}</TableEmpty> : null}
        </TableBody>
      </Table>
    </>
  );
}
