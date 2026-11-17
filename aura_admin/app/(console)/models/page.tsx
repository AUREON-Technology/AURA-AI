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

function stageTone(stage: string) {
  if (stage === "production") return "ok" as const;
  if (stage === "canary" || stage === "shadow") return "warn" as const;
  if (stage === "candidate") return "brand" as const;
  return "neutral" as const;
}
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
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }, []);

  return (
    <>
      <PageHeader title="Models">
        Registry stages and allowlisted local weights. Production needs a MODEL_PROMOTION approval.
      </PageHeader>
      {error ? <Alert>{error}</Alert> : null}

      {approvals.length > 0 ? (
        <Card className="mb-4 border-ok/30">
          <CardHeader className="mb-3">
            <CardTitle>Approved fine-tunes ready to launch</CardTitle>
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
        </Card>
      ) : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Artefact</TableHead>
            <TableHead>Digest</TableHead>
            <TableHead>Canary</TableHead>
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
                    await api(`/admin/models/${row.model_id}/rollback`, {
                      method: "POST",
                      body: JSON.stringify({ to_version: row.version }),
                    });
                    await load();
                  }}
                >
                  Rollback
                </ConfirmDialog>
                </div>
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={6} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={6}>No model versions registered.</TableEmpty> : null}
        </TableBody>
      </Table>

      {me?.is_super_admin ? (
        <Card className="mt-4">
          <CardHeader>
            <div>
              <CardTitle>Import artefact</CardTitle>
              <CardDescription>
                Register a local Ollama tag trained elsewhere. Requires an approved FINETUNE approval
                bound to the dataset digest.
              </CardDescription>
            </div>
          </CardHeader>
          <div className="grid gap-2 desk:grid-cols-[1fr_1fr_1fr_auto]">
            <Input
              aria-label="Artefact URI"
              value={importUri}
              onChange={(e) => setImportUri(e.target.value)}
              placeholder="artefact uri (aura-ft:v1)"
            />
            <Input
              aria-label="Dataset digest"
              value={importDigest}
              onChange={(e) => setImportDigest(e.target.value)}
              placeholder="dataset digest"
            />
            <Input
              aria-label="Approval id"
              value={importApproval}
              onChange={(e) => setImportApproval(e.target.value)}
              placeholder="approval id"
            />
            <ConfirmDialog
              size="md"
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
        </Card>
      ) : null}

      <Card className="mt-4">
        <CardHeader className="mb-3">
          <CardTitle>Local Ollama</CardTitle>
        </CardHeader>
        <ul className="divide-y divide-line">
          {local.map((model) => (
            <li key={model.name} className="flex items-center justify-between gap-2 py-2 first:pt-0">
              <code className="text-[13px]">{model.name}</code>
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
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              className="w-full max-w-xs"
              value={pullName}
              onChange={(e) => setPullName(e.target.value)}
              placeholder="allowlisted name"
              aria-label="Allowlisted model name"
            />
            <ConfirmDialog
              size="md"
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
      </Card>
    </>
  );
}
