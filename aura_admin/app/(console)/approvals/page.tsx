"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api } from "@/lib/api";
import type { Approval } from "@/lib/types";

export default function ApprovalsPage() {
  const [rows, setRows] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api<{ approvals: Approval[] }>("/admin/approvals")
      .then((body) => setRows(body.approvals))
      .catch((err: Error) => setError(err.message));
  }

  useEffect(load, []);

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
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.summary}</td>
              <td>{row.kind}</td>
              <td>{row.status}</td>
              <td>
                {row.status === "pending" ? (
                  <>
                    <ConfirmDialog
                      title="Approve"
                      confirmText="approve"
                      onConfirm={async () => {
                        await api(`/admin/approvals/${row.id}/decide`, {
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
                        await api(`/admin/approvals/${row.id}/decide`, {
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
          ))}
        </tbody>
      </table>
    </>
  );
}
