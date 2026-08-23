"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import type { Me, Operator, OperatorRole } from "@/lib/types";

export default function AccessPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Operator[]>([]);
  const [forgetSubject, setForgetSubject] = useState("");
  const [counts, setCounts] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [who, body] = await Promise.all([
      fetchMe(),
      api<{ operators: Operator[] }>("/admin/operators"),
    ]);
    setMe(who);
    setRows(body.operators);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Access</h1>
      <p className="sub">Operator directory. Role changes and erasure are SuperAdmin only.</p>
      {error ? <div className="banner">{error}</div> : null}
      <table className="table">
        <thead>
          <tr>
            <th>Email</th>
            <th>Role</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.email}>
              <td>{row.email}</td>
              <td>{row.role}</td>
              <td>
                {me?.is_super_admin ? (
                  <>
                    {(["Developer", "Admin", "SuperAdmin"] as OperatorRole[]).map((role) => (
                      <ConfirmDialog
                        key={role}
                        title={`Set ${row.email} to ${role}`}
                        confirmText={role}
                        onConfirm={async () => {
                          await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                            method: "PATCH",
                            body: JSON.stringify({ role }),
                          });
                          await load();
                        }}
                      >
                        {`Make ${role}`}
                      </ConfirmDialog>
                    ))}
                    <ConfirmDialog
                      danger
                      title="Revoke sessions"
                      confirmText="revoke"
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/revoke-sessions`, {
                          method: "POST",
                        });
                      }}
                    >
                      Revoke sessions
                    </ConfirmDialog>
                  </>
                ) : (
                  <span className="muted">Read only</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {me?.is_super_admin ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>GDPR forget</h2>
          <p className="muted">Returns counts only. Never displays user content.</p>
          <input
            value={forgetSubject}
            onChange={(e) => setForgetSubject(e.target.value)}
            placeholder="email or auth subject"
          />
          <ConfirmDialog
            danger
            title="Forget user"
            confirmText="forget"
            onConfirm={async () => {
              const body = await api<{ counts: Record<string, number> }>(
                `/admin/users/${encodeURIComponent(forgetSubject)}/forget`,
                { method: "POST" },
              );
              setCounts(JSON.stringify(body.counts));
            }}
          >
            Forget
          </ConfirmDialog>
          {counts ? <p data-testid="forget-counts">{counts}</p> : null}
        </div>
      ) : null}
    </>
  );
}
