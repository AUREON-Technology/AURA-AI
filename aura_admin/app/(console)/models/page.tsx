"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import type { Me, ModelVersion } from "@/lib/types";

export default function ModelsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<ModelVersion[]>([]);
  const [local, setLocal] = useState<{ name: string }[]>([]);
  const [pullName, setPullName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [who, models, locals] = await Promise.all([
      fetchMe(),
      api<{ models: ModelVersion[] }>("/admin/models"),
      api<{ models: { name: string }[] }>("/admin/local-models"),
    ]);
    setMe(who);
    setRows(models.models);
    setLocal(locals.models ?? []);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Models</h1>
      <p className="sub">Registry stages and allowlisted local weights.</p>
      {error ? <div className="banner">{error}</div> : null}
      <table className="table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Stage</th>
            <th>Canary</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.model_id}@${row.version}`}>
              <td>
                {row.model_id}@{row.version}
              </td>
              <td>{row.stage}</td>
              <td>{row.canary_percent}</td>
              <td>
                {row.stage !== "production" && row.stage !== "archived" ? (
                  <ConfirmDialog
                    title={`Promote ${row.model_id}@${row.version}`}
                    confirmText="promote"
                    onConfirm={async () => {
                      const next =
                        row.stage === "candidate"
                          ? "shadow"
                          : row.stage === "shadow"
                            ? "canary"
                            : "production";
                      await api(
                        `/admin/models/${encodeURIComponent(row.model_id)}/transition?version=${encodeURIComponent(row.version)}`,
                        {
                          method: "POST",
                          body: JSON.stringify({
                            to: next,
                            approval_id: row.approval_id,
                          }),
                        },
                      );
                      await load();
                    }}
                  >
                    Promote
                  </ConfirmDialog>
                ) : null}
                <ConfirmDialog
                  title="Rollback to this version"
                  confirmText="rollback"
                  onConfirm={async () => {
                    await api(`/admin/models/${row.model_id}/rollback`, {
                      method: "POST",
                      body: JSON.stringify({ to_version: row.version }),
                    });
                    await load();
                  }}
                >
                  Rollback
                </ConfirmDialog>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Local Ollama</h2>
        <ul>
          {local.map((model) => (
            <li key={model.name}>
              {model.name}
              {me?.is_super_admin ? (
                <ConfirmDialog
                  danger
                  title={`Delete ${model.name}`}
                  confirmText={model.name}
                  onConfirm={async () => {
                    await api(`/admin/local-models/${encodeURIComponent(model.name)}`, {
                      method: "DELETE",
                    });
                    await load();
                  }}
                >
                  Delete
                </ConfirmDialog>
              ) : null}
            </li>
          ))}
        </ul>
        {me?.is_super_admin ? (
          <div className="row">
            <input value={pullName} onChange={(e) => setPullName(e.target.value)} placeholder="allowlisted name" />
            <ConfirmDialog
              title="Pull model"
              confirmText={pullName || "pull"}
              onConfirm={async () => {
                await api("/admin/local-models/pull", {
                  method: "POST",
                  body: JSON.stringify({ name: pullName }),
                });
                await load();
              }}
            >
              Pull
            </ConfirmDialog>
          </div>
        ) : null}
      </div>
    </>
  );
}
