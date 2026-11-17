"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import type { Me, ModelVersion } from "@/lib/types";

type Approval = { id: string; kind: string; status: string; artefact_digest: string };

export default function ModelsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<ModelVersion[]>([]);
  const [local, setLocal] = useState<{ name: string }[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [promotionApprovals, setPromotionApprovals] = useState<Approval[]>([]);
  const [pullName, setPullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [importUri, setImportUri] = useState("");
  const [importDigest, setImportDigest] = useState("");
  const [importApproval, setImportApproval] = useState("");

  async function load() {
    const [who, models, locals, pending, promotions] = await Promise.all([
      fetchMe(),
      api<{ models: ModelVersion[] }>("/admin/models"),
      api<{ models: { name: string }[] }>("/admin/local-models"),
      api<{ approvals: Approval[] }>("/admin/approvals?status=approved&kind=finetune").catch(
        () => ({ approvals: [] as Approval[] }),
      ),
      api<{ approvals: Approval[] }>(
        "/admin/approvals?status=approved&kind=model_promotion",
      ).catch(() => ({ approvals: [] as Approval[] })),
    ]);
    setMe(who);
    setRows(models.models);
    setLocal(locals.models ?? []);
    setApprovals(pending.approvals ?? []);
    setPromotionApprovals(promotions.approvals ?? []);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Models</h1>
      <p className="sub">Registry stages and allowlisted local weights. Production needs a MODEL_PROMOTION approval.</p>
      {error ? <div className="banner">{error}</div> : null}

      {approvals.length > 0 ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Approved fine-tunes ready to launch</h2>
          <ul>
            {approvals.map((row) => (
              <li key={row.id}>
                  {row.id} · {row.artefact_digest} · {row.status}{" "}
                  {me?.is_super_admin ? (
                    <ConfirmDialog
                      title={`Launch train for ${row.id}`}
                      confirmText="launch"
                      onConfirm={async () => {
                        await api("/admin/learning/jobs", {
                          method: "POST",
                          body: JSON.stringify({
                            approval_id: row.id,
                            run_inline: true,
                          }),
                        });
                        await load();
                      }}
                    >
                      Launch train
                    </ConfirmDialog>
                  ) : null}
                </li>
              ))}
          </ul>
        </div>
      ) : null}

      <table className="table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Stage</th>
            <th>Artefact</th>
            <th>Digest</th>
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
              <td>{(row as ModelVersion & { artefact_uri?: string }).artefact_uri || "—"}</td>
              <td>{row.dataset_digest || "—"}</td>
              <td>{row.canary_percent}</td>
              <td>
                {row.stage === "canary" || row.stage === "shadow" ? (
                  <ConfirmDialog
                    title={`Request production approval for ${row.model_id}@${row.version}`}
                    confirmText="request"
                    onConfirm={async () => {
                      await api(
                        `/admin/models/${encodeURIComponent(row.model_id)}/request-promotion?version=${encodeURIComponent(row.version)}`,
                        {
                          method: "POST",
                          body: JSON.stringify({
                            model_id: row.model_id,
                            version: row.version,
                          }),
                        },
                      );
                      await load();
                    }}
                  >
                    Request promote
                  </ConfirmDialog>
                ) : null}
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
                      // Production must use a MODEL_PROMOTION approval id bound to
                      // model_id@version — never the FINETUNE (train) approval_id.
                      const body: { to: string; approval_id?: string | null; canary_percent?: number } = {
                        to: next,
                      };
                      if (next === "canary") {
                        body.canary_percent = 10;
                      }
                      if (next === "production") {
                        const key = `${row.model_id}@${row.version}`;
                        const promo = promotionApprovals.find(
                          (a) => a.artefact_digest === key,
                        );
                        if (!promo) {
                          throw new Error(
                            `No approved MODEL_PROMOTION for ${key}. Use “Request promote”, approve it, then Promote.`,
                          );
                        }
                        body.approval_id = promo.id;
                      }
                      await api(
                        `/admin/models/${encodeURIComponent(row.model_id)}/transition?version=${encodeURIComponent(row.version)}`,
                        {
                          method: "POST",
                          body: JSON.stringify(body),
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
                    await api(`/admin/models/${encodeURIComponent(row.model_id)}/rollback`, {
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

      {me?.is_super_admin ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Import artefact</h2>
          <p className="muted">
            Register a local Ollama tag trained elsewhere. Requires an approved FINETUNE approval
            bound to the dataset digest.
          </p>
          <div className="row">
            <input
              value={importUri}
              onChange={(e) => setImportUri(e.target.value)}
              placeholder="artefact uri (aura-ft:v1)"
            />
            <input
              value={importDigest}
              onChange={(e) => setImportDigest(e.target.value)}
              placeholder="dataset digest"
            />
            <input
              value={importApproval}
              onChange={(e) => setImportApproval(e.target.value)}
              placeholder="approval id"
            />
            <ConfirmDialog
              title="Import model artefact"
              confirmText="import"
              onConfirm={async () => {
                await api("/admin/models/import", {
                  method: "POST",
                  body: JSON.stringify({
                    version: `v${Date.now().toString(36)}`,
                    artefact_uri: importUri,
                    dataset_digest: importDigest,
                    approval_id: importApproval,
                  }),
                });
                await load();
              }}
            >
              Import
            </ConfirmDialog>
          </div>
        </div>
      ) : null}

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
