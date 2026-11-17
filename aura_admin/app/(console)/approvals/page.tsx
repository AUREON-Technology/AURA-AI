"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api, fetchMe } from "@/lib/api";
import type { Approval, Me } from "@/lib/types";

// Track AP's gates are a Super Admin's decision (ADR-021, ADR-024); the gateway refuses anyone
// else with 403 (aura_core/api/admin.py, audit INT-006), so the console does not offer them.
const SUPER_ADMIN_KINDS = new Set(["connector_registration", "blueprint_deployment"]);

function statusTone(status: string) {
  if (status === "approved") return "ok" as const;
  if (status === "rejected" || status === "expired") return "bad" as const;
  if (status === "pending") return "warn" as const;
  return "neutral" as const;
}

export default function ApprovalsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  function load() {
    api<{ approvals: Approval[] }>("/admin/approvals")
      .then((body) => setRows(body.approvals))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }

  useEffect(() => {
    load();
    fetchMe()
      .then(setMe)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <PageHeader title="Approvals">
        Human gate for model and source changes. Approvals expire after 14 days.
      </PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Summary</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Digest</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Reasons</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const reasons = Array.isArray(row.payload?.reasons)
              ? (row.payload?.reasons as string[])
              : [];
            return (
              <TableRow key={row.id}>
                <TableCell className="font-medium">{row.summary}</TableCell>
                <TableCell className="text-muted">{row.kind}</TableCell>
                <TableCell>
                  <code className="text-xs">{row.artefact_digest}</code>
                </TableCell>
                <TableCell>
                  <Badge tone={statusTone(row.status)} dot>
                    {row.status}
                  </Badge>
                </TableCell>
                <TableCell>
                  {reasons.length ? (
                    <ul className="list-disc space-y-0.5 pl-4 text-[13px] text-muted">
                      {reasons.slice(0, 3).map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell>
                  {row.status === "pending" && SUPER_ADMIN_KINDS.has(row.kind) && !me?.is_super_admin ? (
                    <span className="text-[13px] text-muted">SuperAdmin decides</span>
                  ) : row.status === "pending" ? (
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <ConfirmDialog
                        title="Approve"
                        confirmText="approve"
                        onConfirm={async () => {
                          await api(`/admin/approvals/${encodeURIComponent(row.id)}/decide`, {
                            method: "POST",
                            body: JSON.stringify({ approved: true }),
                          });
                          load();
                        }}
                      >
                        Approve
                      </ConfirmDialog>
                      <ConfirmDialog
                        danger
                        title="Reject"
                        confirmText="reject"
                        onConfirm={async () => {
                          await api(`/admin/approvals/${encodeURIComponent(row.id)}/decide`, {
                            method: "POST",
                            body: JSON.stringify({ approved: false }),
                          });
                          load();
                        }}
                      >
                        Reject
                      </ConfirmDialog>
                    </div>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
          {!loaded ? <TableSkeleton cols={6} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={6}>Nothing is waiting for a decision.</TableEmpty> : null}
        </TableBody>
      </Table>
    </>
  );
}
