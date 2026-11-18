"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { Me, ModelVersion } from "@/lib/types";

type Approval = { id: string; kind: string; status: string; artefact_digest: string };

export default function ModelsPage() {
  const { t, errorText } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<ModelVersion[]>([]);
  const [local, setLocal] = useState<{ name: string }[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [promotionApprovals, setPromotionApprovals] = useState<Approval[]>([]);
  const [pullName, setPullName] = useState("");
  const [error, setError] = useState<unknown>(null);
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
    load().catch((err: unknown) => setError(err));
  }, []);

  return (
    <>
      <h1>{t("nav.models")}</h1>
      <p className="sub">{t("models.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}

      {approvals.length > 0 ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>{t("models.approvedFinetunes")}</h2>
          <ul>
            {approvals.map((row) => (
              <li key={row.id}>
                  {row.id} · {row.artefact_digest} · {row.status}{" "}
                  {me?.is_super_admin ? (
                    <ConfirmDialog
                      title={t("models.launchTitle", { id: row.id })}
                      confirmText={t("models.launchWord")}
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
                      {t("models.launch")}
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
            <th>{t("models.colModel")}</th>
            <th>{t("models.colStage")}</th>
            <th>{t("common.artefact")}</th>
            <th>{t("common.digest")}</th>
            <th>{t("models.colCanary")}</th>
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
                    title={t("models.requestTitle", { model: `${row.model_id}@${row.version}` })}
                    confirmText={t("common.requestWord")}
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
                    {t("models.requestPromote")}
                  </ConfirmDialog>
                ) : null}
                {row.stage !== "production" && row.stage !== "archived" ? (
                  <ConfirmDialog
                    title={t("models.promoteTitle", { model: `${row.model_id}@${row.version}` })}
                    confirmText={t("models.promoteWord")}
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
                          throw new Error(t("models.noPromotion", { key }));
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
                    {t("models.promote")}
                  </ConfirmDialog>
                ) : null}
                <ConfirmDialog
                  title={t("models.rollbackTitle")}
                  confirmText={t("common.rollbackWord")}
                  onConfirm={async () => {
                    await api(`/admin/models/${encodeURIComponent(row.model_id)}/rollback`, {
                      method: "POST",
                      body: JSON.stringify({ to_version: row.version }),
                    });
                    await load();
                  }}
                >
                  {t("common.rollback")}
                </ConfirmDialog>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {me?.is_super_admin ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>{t("models.importHeading")}</h2>
          <p className="muted">{t("models.importHelp")}</p>
          <div className="row">
            <input
              value={importUri}
              onChange={(e) => setImportUri(e.target.value)}
              placeholder={t("models.importUriPlaceholder")}
            />
            <input
              value={importDigest}
              onChange={(e) => setImportDigest(e.target.value)}
              placeholder={t("models.datasetDigestPlaceholder")}
            />
            <input
              value={importApproval}
              onChange={(e) => setImportApproval(e.target.value)}
              placeholder={t("models.approvalIdPlaceholder")}
            />
            <ConfirmDialog
              title={t("models.importTitle")}
              confirmText={t("models.importWord")}
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
              {t("models.import")}
            </ConfirmDialog>
          </div>
        </div>
      ) : null}

      <div className="card" style={{ marginTop: 16 }}>
        <h2>{t("models.localOllama")}</h2>
        <ul>
          {local.map((model) => (
            <li key={model.name}>
              {model.name}
              {me?.is_super_admin ? (
                <ConfirmDialog
                  danger
                  title={t("common.deleteTitle", { name: model.name })}
                  confirmText={model.name}
                  onConfirm={async () => {
                    await api(`/admin/local-models/${encodeURIComponent(model.name)}`, {
                      method: "DELETE",
                    });
                    await load();
                  }}
                >
                  {t("common.delete")}
                </ConfirmDialog>
              ) : null}
            </li>
          ))}
        </ul>
        {me?.is_super_admin ? (
          <div className="row">
            <input value={pullName} onChange={(e) => setPullName(e.target.value)} placeholder={t("models.allowlistedPlaceholder")} />
            <ConfirmDialog
              title={t("models.pullTitle")}
              confirmText={pullName || t("models.pullWord")}
              onConfirm={async () => {
                await api("/admin/local-models/pull", {
                  method: "POST",
                  body: JSON.stringify({ name: pullName }),
                });
                await load();
              }}
            >
              {t("models.pull")}
            </ConfirmDialog>
          </div>
        ) : null}
      </div>
    </>
  );
}
