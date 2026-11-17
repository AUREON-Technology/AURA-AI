"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { AuditEvent } from "@/lib/types";

export default function AuditPage() {
  const { t, errorText } = useI18n();
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [action, setAction] = useState("");
  const [error, setError] = useState<unknown>(null);

  // Stable across renders (state setters are), so the mount-time effect below can list it as its
  // one dependency instead of silencing react-hooks/exhaustive-deps (audit G-10 lint gate).
  const load = useCallback((next: string) => {
    const query = next ? `?action=${encodeURIComponent(next)}` : "";
    api<{ events: AuditEvent[] }>(`/admin/audit${query}`)
      .then((body) => setRows(body.events))
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  return (
    <>
      <h1>{t("nav.audit")}</h1>
      <p className="sub">{t("audit.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      <div className="row">
        <input value={action} onChange={(e) => setAction(e.target.value)} placeholder={t("audit.actionPlaceholder")} />
        <button className="btn" type="button" onClick={() => load(action)}>
          {t("audit.filter")}
        </button>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>{t("audit.colWhen")}</th>
            <th>{t("audit.colActor")}</th>
            <th>{t("audit.colAction")}</th>
            <th>{t("audit.colResource")}</th>
            <th>{t("audit.colOutcome")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.at}-${index}`}>
              <td>{row.at}</td>
              <td>
                {row.actor} ({row.actor_role})
              </td>
              <td>{row.action}</td>
              <td>{row.resource}</td>
              <td>{row.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
