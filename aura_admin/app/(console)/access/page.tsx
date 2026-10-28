"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import type { Me, Operator, OperatorRole } from "@/lib/types";

export default function AccessPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Operator[]>([]);
  const [forgetSubject, setForgetSubject] = useState("");
  const [grantEmail, setGrantEmail] = useState("");
  const [grantRole, setGrantRole] = useState<OperatorRole>("Developer");
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
      <p className="sub">
        Operator directory. Role changes and erasure are SuperAdmin only. A change applies at the
        person&apos;s next sign-in or token refresh (within 15 minutes). A role granted by an env
        list or roles.json can only be lowered there.
      </p>
      {error ? <div className="banner">{error}</div> : null}
      {me?.is_super_admin ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Grant a role</h2>
          <p className="muted">The person must have signed in once.</p>
          <input
            value={grantEmail}
            onChange={(e) => setGrantEmail(e.target.value)}
            placeholder="email"
          />
          <select value={grantRole} onChange={(e) => setGrantRole(e.target.value as OperatorRole)}>
            {(["Developer", "Admin", "SuperAdmin"] as OperatorRole[]).map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
          <ConfirmDialog
            title={`Set ${grantEmail || "this user"} to ${grantRole}`}
            confirmText={grantRole}
            disabled={!grantEmail.trim()}
            onConfirm={async () => {
              await api(`/admin/operators/${encodeURIComponent(grantEmail.trim())}/role`, {
                method: "PATCH",
                body: JSON.stringify({ role: grantRole }),
              });
              setGrantEmail("");
              await load();
            }}
          >
            Grant role
          </ConfirmDialog>
        </div>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>Email</th>
            <th>Role</th>
            <th>Granted by</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.email}>
              <td>{row.email}</td>
              <td>{row.role}</td>
              <td>{row.granted_by ?? ""}</td>
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
                      title={`Remove ${row.email}'s Access-page role`}
                      confirmText="User"
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                          method: "PATCH",
                          body: JSON.stringify({ role: "User" }),
                        });
                        await load();
                      }}
                    >
                      Remove access
                    </ConfirmDialog>
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
