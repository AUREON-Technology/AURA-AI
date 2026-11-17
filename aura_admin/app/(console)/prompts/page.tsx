"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { AdminStatus, PinResult, PromptRow } from "@/lib/types";

export default function PromptsPage() {
  const { t, errorText } = useI18n();
  const [rows, setRows] = useState<PromptRow[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => setRows(status.prompts ?? []))
      .catch((err: unknown) => setError(err));
  }

  useEffect(load, []);

  return (
    <>
      <h1>{t("nav.prompts")}</h1>
      <p className="sub">{t("prompts.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      {notice ? (
        <p className="muted" data-testid="prompt-notice">
          {notice}
        </p>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>{t("prompts.colTemplate")}</th>
            <th>{t("common.active")}</th>
            <th>{t("prompts.colPinned")}</th>
            <th>{t("prompts.colChecksum")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.id}</td>
              <td>{row.active}</td>
              <td>{row.pinned ?? "—"}</td>
              <td>{row.checksum}</td>
              <td>
                {(row.versions ?? []).map((version) => (
                  <ConfirmDialog
                    key={version}
                    title={t("prompts.pinTitle", { target: `${row.id}@${version}` })}
                    confirmText={version}
                    onConfirm={async () => {
                      const result = await api<PinResult>(
                        `/admin/prompts/${encodeURIComponent(row.id)}/pin`,
                        { method: "POST", body: JSON.stringify({ version }) },
                      );
                      setNotice(
                        result?.shared
                          ? t("prompts.pinnedShared", {
                              target: result.pinned,
                              seconds: result.propagation_s ?? 5,
                            })
                          : t("prompts.pinned", { target: result?.pinned ?? `${row.id}@${version}` }),
                      );
                      load();
                    }}
                  >
                    {t("prompts.pin", { version })}
                  </ConfirmDialog>
                ))}
                {row.pinned ? (
                  <ConfirmDialog
                    title={t("prompts.unpinTitle", { id: row.id })}
                    confirmText={t("prompts.unpinWord")}
                    onConfirm={async () => {
                      await api(`/admin/prompts/${encodeURIComponent(row.id)}/pin`, { method: "DELETE" });
                      setNotice(t("prompts.unpinned", { id: row.id }));
                      load();
                    }}
                  >
                    {t("prompts.unpin")}
                  </ConfirmDialog>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
