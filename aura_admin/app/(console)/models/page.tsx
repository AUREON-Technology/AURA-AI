"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { Me, ModelVersion } from "@/lib/types";

function stageTone(stage: string) {
  if (stage === "production") return "ok" as const;
  if (stage === "canary" || stage === "shadow") return "warn" as const;
  if (stage === "candidate") return "brand" as const;
  return "neutral" as const;
}

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
  const [loaded, setLoaded] = useState(false);
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
    load()
      .catch((err: unknown) => setError(err))
      .finally(() => setLoaded(true));
  }, []);

  return (
    <>
      <PageHeader title={t("nav.models")}>{t("models.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}

      {approvals.length > 0 ? (
        <Card className="mb-4 border-ok/30">
          <CardHeader className="mb-3">
            <CardTitle>{t("models.approvedFinetunes")}</CardTitle>
          </CardHeader>
          <ul className="divide-y divide-line">
            {approvals.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
                  <span className="text-[13.5px]">
                    <span className="font-medium">{row.id}</span>{" "}
                    <span className="text-muted">
                      · <code className="text-xs">{row.artefact_digest}</code> · {row.status}
                    </span>
                  </span>{" "}
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
        </Card>
      ) : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("models.colModel")}</TableHead>
            <TableHead>{t("models.colStage")}</TableHead>
            <TableHead>{t("common.artefact")}</TableHead>
            <TableHead>{t("common.digest")}</TableHead>
            <TableHead>{t("models.colCanary")}</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={`${row.model_id}@${row.version}`}>
              <TableCell className="font-medium">
                {row.model_id}@{row.version}
              </TableCell>
              <TableCell>
                <Badge tone={stageTone(row.stage)} dot pulse={row.stage === "production"}>
                  {row.stage}
                </Badge>
              </TableCell>
              <TableCell className="text-muted">{(row as ModelVersion & { artefact_uri?: string }).artefact_uri || "—"}</TableCell>
              <TableCell>
                <code className="text-xs">{row.dataset_digest || "—"}</code>
              </TableCell>
              <TableCell>{row.canary_percent}</TableCell>
              <TableCell>
                <div className="flex flex-wrap justify-end gap-1.5">
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
                </div>
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={6} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={6}>{t("models.empty")}</TableEmpty> : null}
        </TableBody>
      </Table>

      {me?.is_super_admin ? (
        <Card className="mt-4">
          <CardHeader>
            <div>
              <CardTitle>{t("models.importHeading")}</CardTitle>
              <CardDescription>{t("models.importHelp")}</CardDescription>
            </div>
          </CardHeader>
          <div className="grid gap-2 desk:grid-cols-[1fr_1fr_1fr_auto]">
            <Input
              aria-label={t("models.importUriPlaceholder")}
              value={importUri}
              onChange={(e) => setImportUri(e.target.value)}
              placeholder={t("models.importUriPlaceholder")}
            />
            <Input
              aria-label={t("models.datasetDigestPlaceholder")}
              value={importDigest}
              onChange={(e) => setImportDigest(e.target.value)}
              placeholder={t("models.datasetDigestPlaceholder")}
            />
            <Input
              aria-label={t("models.approvalIdPlaceholder")}
              value={importApproval}
              onChange={(e) => setImportApproval(e.target.value)}
              placeholder={t("models.approvalIdPlaceholder")}
            />
            <ConfirmDialog
              size="md"
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
        </Card>
      ) : null}

      <Card className="mt-4">
        <CardHeader className="mb-3">
          <CardTitle>{t("models.localOllama")}</CardTitle>
        </CardHeader>
        <ul className="divide-y divide-line">
          {local.map((model) => (
            <li key={model.name} className="flex items-center justify-between gap-2 py-2 first:pt-0">
              <code className="text-[13px]">{model.name}</code>
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
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              className="w-full max-w-xs"
              value={pullName}
              onChange={(e) => setPullName(e.target.value)}
              placeholder={t("models.allowlistedPlaceholder")}
              aria-label={t("models.allowlistedPlaceholder")}
            />
            <ConfirmDialog
              size="md"
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
      </Card>
    </>
  );
}
