"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { Approval, Me } from "@/lib/types";

// Track AP's gates are a Super Admin's decision (ADR-021, ADR-024); the gateway refuses anyone
// else with 403 (aura_core/api/admin.py, audit INT-006), so the console does not offer them.
const SUPER_ADMIN_KINDS = new Set(["connector_registration", "blueprint_deployment"]);

export default function ApprovalsPage() {
  const { t, errorText } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Approval[]>([]);
  const [error, setError] = useState<unknown>(null);

  function load() {
    api<{ approvals: Approval[] }>("/admin/approvals")
      .then((body) => setRows(body.approvals))
      .catch((err: unknown) => setError(err));
  }

  useEffect(() => {
    load();
    fetchMe()
      .then(setMe)
      .catch((err: unknown) => setError(err));
  }, []);

  return (
    <>
      <h1>{t("nav.approvals")}</h1>
      <p className="sub">{t("approvals.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      <table className="table">
        <thead>
          <tr>
            <th>{t("approvals.colSummary")}</th>
            <th>{t("approvals.colKind")}</th>
            <th>{t("common.digest")}</th>
            <th>{t("common.status")}</th>
            <th>{t("approvals.colReasons")}</th>
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
                    <span className="muted">{t("approvals.superAdminDecides")}</span>
                  ) : row.status === "pending" ? (
                    <>
                      <ConfirmDialog
                        title={t("approvals.approve")}
                        confirmText={t("approvals.approveWord")}
                        onConfirm={async () => {
                          await api(`/admin/approvals/${encodeURIComponent(row.id)}/decide`, {
                            method: "POST",
                            body: JSON.stringify({ approved: true }),
                          });
                          load();
                        }}
                      >
                        {t("approvals.approve")}
                      </ConfirmDialog>
                      <ConfirmDialog
                        danger
                        title={t("approvals.reject")}
                        confirmText={t("approvals.rejectWord")}
                        onConfirm={async () => {
                          await api(`/admin/approvals/${encodeURIComponent(row.id)}/decide`, {
                            method: "POST",
                            body: JSON.stringify({ approved: false }),
                          });
                          load();
                        }}
                      >
                        {t("approvals.reject")}
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
