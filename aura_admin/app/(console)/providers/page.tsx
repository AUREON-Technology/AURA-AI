"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusPill } from "@/components/status-pill";
import { Alert } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { AdminStatus, ProviderRow } from "@/lib/types";

export default function ProvidersPage() {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => setRows(status.providers ?? []))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }

  useEffect(load, []);

  return (
    <>
      <PageHeader title="Providers">Circuit state from the kernel router.</PageHeader>
      {error ? <Alert>{error}</Alert> : null}
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
                    await api(`/admin/providers/${row.provider}/reset-circuit`, { method: "POST" });
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
