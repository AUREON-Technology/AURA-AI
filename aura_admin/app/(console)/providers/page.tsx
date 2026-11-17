"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusPill } from "@/components/status-pill";
import { Alert } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { AdminStatus, ProviderRow, ResetCircuitResult } from "@/lib/types";

export default function ProvidersPage() {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [scope, setScope] = useState<{ scope?: string; pid?: number }>({});
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => {
        setRows(status.providers ?? []);
        setScope({ scope: status.providers_scope, pid: status.worker_pid });
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }

  useEffect(load, []);

  return (
    <>
      <PageHeader title="Providers">Circuit state from the kernel router.</PageHeader>
      {/* Breakers live in each gateway worker's memory: this table and a reset reach only the
          worker that answered, so say so rather than imply a deployment-wide view. */}
      {scope.scope === "worker" ? (
        <p className="mb-4 text-[13px] text-muted" data-testid="providers-scope">
          Per-worker state: this view and any reset apply to gateway worker
          {scope.pid !== undefined ? ` ${scope.pid}` : ""} only; other workers keep their own breakers.
        </p>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
      {notice ? (
        <p className="mb-4 text-[13px] text-muted motion-safe:animate-fade-up" data-testid="reset-notice">
          {notice}
        </p>
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Provider</TableHead>
            <TableHead>State</TableHead>
            <TableHead>Failure rate</TableHead>
            <TableHead>Latency</TableHead>
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
              <TableCell className="text-right">
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
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={5} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={5}>No providers reported yet.</TableEmpty> : null}
        </TableBody>
      </Table>
    </>
  );
}
