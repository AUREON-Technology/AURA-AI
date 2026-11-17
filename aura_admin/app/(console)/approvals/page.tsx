"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import type { Approval, Me } from "@/lib/types";

// Track AP's gates are a Super Admin's decision (ADR-021, ADR-024); the gateway refuses anyone
// else with 403 (aura_core/api/admin.py, audit INT-006), so the console does not offer them.
const SUPER_ADMIN_KINDS = new Set(["connector_registration", "blueprint_deployment"]);

export default function ApprovalsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api<{ approvals: Approval[] }>("/admin/approvals")
      .then((body) => setRows(body.approvals))
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    load();
    fetchMe()
      .then(setMe)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Approvals</h1>
      <p className="sub">Human gate for model and source changes. Approvals expire after 14 days.</p>
      {error ? <div className="banner">{error}</div> : null}
      <table className="table">
        <thead>
          <tr>
            <th>Summary</th>
            <th>Kind</th>
            <th>Digest</th>
            <th>Status</th>
            <th>Reasons</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const reasons = Array.isArray(row.payload?.reasons)
              ? (row.payload?.reasons as string[])
              : [];
            return (
              <tr key={row.id}>
                <td>{row.summary}</td>
                <td>{row.kind}</td>
                <td>
                  <code>{row.artefact_digest}</code>
                </td>
                <td>{row.status}</td>
                <td>
                  {reasons.length ? (
                    <ul>
                      {reasons.slice(0, 3).map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  {row.status === "pending" && SUPER_ADMIN_KINDS.has(row.kind) && !me?.is_super_admin ? (
                    <span className="muted">SuperAdmin decides</span>
                  ) : row.status === "pending" ? (
                    <>
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
                    </>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
