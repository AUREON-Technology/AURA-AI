"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api } from "@/lib/api";
import type { AdminStatus, PinResult, PromptRow } from "@/lib/types";

export default function PromptsPage() {
  const [rows, setRows] = useState<PromptRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => setRows(status.prompts ?? []))
      .catch((err: Error) => setError(err.message));
  }

  useEffect(load, []);

  return (
    <>
      <h1>Prompts</h1>
      <p className="sub">Pin a previous version to roll back a bad change. No deploy required.</p>
      {error ? <div className="banner">{error}</div> : null}
      {notice ? (
        <p className="muted" data-testid="prompt-notice">
          {notice}
        </p>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>Template</th>
            <th>Active</th>
            <th>Pinned</th>
            <th>Checksum</th>
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
                    title={`Pin ${row.id}@${version}`}
                    confirmText={version}
                    onConfirm={async () => {
                      const result = await api<PinResult>(
                        `/admin/prompts/${encodeURIComponent(row.id)}/pin`,
                        { method: "POST", body: JSON.stringify({ version }) },
                      );
                      setNotice(
                        result?.shared
                          ? `Pinned ${result.pinned}; applies to all workers within ~${result.propagation_s ?? 5} s.`
                          : `Pinned ${result?.pinned ?? `${row.id}@${version}`}.`,
                      );
                      load();
                    }}
                  >
                    Pin {version}
                  </ConfirmDialog>
                ))}
                {row.pinned ? (
                  <ConfirmDialog
                    title={`Unpin ${row.id}`}
                    confirmText="unpin"
                    onConfirm={async () => {
                      await api(`/admin/prompts/${encodeURIComponent(row.id)}/pin`, { method: "DELETE" });
                      setNotice(`Released the pin on ${row.id}; the active version applies again.`);
                      load();
                    }}
                  >
                    Unpin
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
