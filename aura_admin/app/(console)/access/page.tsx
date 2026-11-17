"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { ForgetResult, Me, Operator, OperatorRole } from "@/lib/types";

export default function AccessPage() {
  const { t, errorText } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Operator[]>([]);
  const [forgetSubject, setForgetSubject] = useState("");
  const [grantEmail, setGrantEmail] = useState("");
  const [grantRole, setGrantRole] = useState<OperatorRole>("Developer");
  const [forgetResult, setForgetResult] = useState<ForgetResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function load() {
    const [who, body] = await Promise.all([
      fetchMe(),
      api<{ operators: Operator[] }>("/admin/operators"),
    ]);
    setMe(who);
    setRows(body.operators);
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err));
  }, []);

  return (
    <>
      <h1>{t("nav.access")}</h1>
      <p className="sub">{t("access.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      {me?.is_super_admin ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>{t("access.grantHeading")}</h2>
          <p className="muted">{t("access.grantHelp")}</p>
          <input
            value={grantEmail}
            onChange={(e) => setGrantEmail(e.target.value)}
            placeholder={t("access.emailPlaceholder")}
          />
          <select value={grantRole} onChange={(e) => setGrantRole(e.target.value as OperatorRole)}>
            {(["Developer", "Admin", "SuperAdmin"] as OperatorRole[]).map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
          <ConfirmDialog
            title={t("access.setRoleTitle", { who: grantEmail || t("access.thisUser"), role: grantRole })}
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
            {t("access.grantRole")}
          </ConfirmDialog>
        </div>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>{t("access.colEmail")}</th>
            <th>{t("access.colRole")}</th>
            <th>{t("access.colGrantedBy")}</th>
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
                        title={t("access.setRoleTitle", { who: row.email, role })}
                        confirmText={role}
                        onConfirm={async () => {
                          await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                            method: "PATCH",
                            body: JSON.stringify({ role }),
                          });
                          await load();
                        }}
                      >
                        {t("access.make", { role })}
                      </ConfirmDialog>
                    ))}
                    <ConfirmDialog
                      danger
                      title={t("access.removeTitle", { email: row.email })}
                      confirmText="User"
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                          method: "PATCH",
                          body: JSON.stringify({ role: "User" }),
                        });
                        await load();
                      }}
                    >
                      {t("access.removeAccess")}
                    </ConfirmDialog>
                    <ConfirmDialog
                      danger
                      title={t("access.revokeSessions")}
                      confirmText={t("access.revokeWord")}
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/revoke-sessions`, {
                          method: "POST",
                        });
                      }}
                    >
                      {t("access.revokeSessions")}
                    </ConfirmDialog>
                  </>
                ) : (
                  <span className="muted">{t("access.readOnly")}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {me?.is_super_admin ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>{t("access.gdprHeading")}</h2>
          <p className="muted">{t("access.gdprHelp")}</p>
          <input
            value={forgetSubject}
            onChange={(e) => setForgetSubject(e.target.value)}
            placeholder={t("access.forgetPlaceholder")}
          />
          <ConfirmDialog
            danger
            title={t("access.forgetTitle")}
            confirmText={t("access.forgetWord")}
            onConfirm={async () => {
              setForgetResult(null);
              const body = await api<ForgetResult>(
                `/admin/users/${encodeURIComponent(forgetSubject)}/forget`,
                { method: "POST" },
              );
              setForgetResult(body);
            }}
          >
            {t("access.forget")}
          </ConfirmDialog>
          {forgetResult ? <ForgetOutcome result={forgetResult} /> : null}
        </div>
      ) : null}
    </>
  );
}

// The gateway answers 200 for a partial erasure too: `forgotten` is false, `failed` names the
// stores it could not clear, and the users row is kept (api/admin_platform.py erase_account).
// Showing only the counts made a partial erasure read as done (audit INT-008).
function ForgetOutcome({ result }: { result: ForgetResult }) {
  const { t } = useI18n();
  const failed = result.failed ?? [];
  const complete = result.forgotten === true && failed.length === 0;
  return (
    <div style={{ marginTop: 12 }}>
      <p
        data-testid="forget-status"
        className={complete ? undefined : "banner"}
        role={complete ? undefined : "alert"}
      >
        {complete
          ? t("access.erasureComplete")
          : failed.length
            ? t("access.erasureFailedFor", { stores: failed.join(", ") })
            : t("access.erasureUnconfirmed")}
      </p>
      <p data-testid="forget-counts">{JSON.stringify(result.counts ?? {})}</p>
      {result.not_erased?.length ? (
        <div data-testid="forget-not-erased">
          <p className="muted">{t("access.notErased")}</p>
          <ul>
            {result.not_erased.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
