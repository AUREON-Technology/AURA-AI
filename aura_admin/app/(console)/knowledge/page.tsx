"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { KnowledgeSource, Me } from "@/lib/types";

/** The latest triggered refresh, as `GET /admin/refresh/latest` returns it (shared across workers). */
type RefreshRun = {
  run_id: string;
  dry_run: boolean;
  status: "running" | "succeeded" | "failed";
  triggered_by: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  report: {
    index_rebuild?: string;
    totals?: Record<string, number>;
    sources?: { id: string; fetched: number; ingested: number; error: string | null; held: string[]; would_apply: boolean | null }[];
  } | null;
};

export default function KnowledgePage() {
  const { t, errorText } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [run, setRun] = useState<RefreshRun | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [indexes, setIndexes] = useState<{ physical_name: string; active: boolean }[]>([]);
  const [id, setId] = useState("");
  const [uri, setUri] = useState("https://");
  const [error, setError] = useState<unknown>(null);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    const [meRow, sourceBody, indexBody, refreshBody] = await Promise.all([
      fetchMe(),
      api<{ sources: KnowledgeSource[] }>("/admin/sources"),
      api<{ indexes: { physical_name: string; active: boolean }[] }>("/admin/indexes"),
      api<{ run: RefreshRun | null }>("/admin/refresh/latest"),
    ]);
    setMe(meRow);
    setSources(sourceBody.sources);
    setIndexes(indexBody.indexes);
    setRun(refreshBody.run);
  }

  async function startRefresh(dryRun: boolean) {
    setError(null);
    try {
      await api("/admin/refresh", { method: "POST", body: JSON.stringify({ dry_run: dryRun }) });
      setRun((await api<{ run: RefreshRun | null }>("/admin/refresh/latest")).run);
    } catch (err) {
      setError(err);
    }
  }

  useEffect(() => {
    load()
      .catch((err: unknown) => setError(err))
      .finally(() => setLoaded(true));
  }, []);

  // While a refresh runs it is a background task on the gateway: follow it until it ends, then
  // reload so the sources and indexes show what it changed.
  useEffect(() => {
    if (run?.status !== "running") return;
    const timer = setInterval(() => {
      load().catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [run?.status]);

  return (
    <>
      <PageHeader title={t("nav.knowledge")}>{t("knowledge.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("knowledge.refresh")}</CardTitle>
            <CardDescription>{t("knowledge.refreshHelp")}</CardDescription>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" disabled={run?.status === "running"} onClick={() => startRefresh(true)}>
              {t("knowledge.refreshDry")}
            </Button>
            {me?.is_super_admin ? (
              <ConfirmDialog
                danger
                title={t("knowledge.refreshTitle")}
                confirmText={t("knowledge.refreshWord")}
                disabled={run?.status === "running"}
                onConfirm={async () => {
                  await startRefresh(false);
                }}
              >
                {t("knowledge.refreshNow")}
              </ConfirmDialog>
            ) : null}
          </div>
        </CardHeader>
        <div className="space-y-1 px-5 pb-4 text-sm">
          {run === null ? (
            <p>{t("knowledge.refreshNever")}</p>
          ) : (
            <>
              <p>
                <Badge tone={run.status === "failed" ? "bad" : run.status === "running" ? "neutral" : "ok"} dot>
                  {t(`knowledge.refreshStatus.${run.status}`)}
                </Badge>{" "}
                {t(run.dry_run ? "knowledge.refreshModeDry" : "knowledge.refreshModeReal")} ·{" "}
                {run.triggered_by} · {run.started_at}
              </p>
              {run.error ? <p>{run.error}</p> : null}
              {run.report?.totals ? (
                <p>
                  {t("knowledge.refreshTotals", {
                    ingested: run.report.totals.ingested ?? 0,
                    skipped: run.report.totals.skipped ?? 0,
                    failed: run.report.totals.failed ?? 0,
                    held: run.report.totals.held ?? 0,
                  })}
                </p>
              ) : null}
              {(run.report?.sources ?? []).map((s) => (
                <p key={s.id}>
                  <strong>{s.id}</strong>:{" "}
                  {s.error
                    ? s.error
                    : s.held.length > 0
                      ? `${t("knowledge.refreshHeld")} (${s.held.join(", ")})`
                      : t("knowledge.refreshSourceOk", { fetched: s.fetched, ingested: s.ingested })}
                </p>
              ))}
            </>
          )}
        </div>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("knowledge.sources")}</CardTitle>
            <CardDescription>{t("knowledge.sourcesHelp")}</CardDescription>
          </div>
        </CardHeader>
        <Table flush>
          <TableHeader>
            <TableRow>
              <TableHead>{t("knowledge.colId")}</TableHead>
              <TableHead>{t("knowledge.colUri")}</TableHead>
              <TableHead>{t("knowledge.colEnabled")}</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sources.map((source) => (
              <TableRow key={source.id}>
                <TableCell className="font-medium">{source.id}</TableCell>
                <TableCell className="max-w-[360px] truncate text-muted">{source.uri}</TableCell>
                <TableCell>
                  <Badge tone={source.enabled ? "ok" : "neutral"} dot>
                    {source.enabled ? t("common.yes") : t("common.no")}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap justify-end gap-1.5">
                    <Button
                      size="sm"
                      onClick={async () => {
                        // Not an unhandled rejection: a failed toggle says why (audit INT-032).
                        setError(null);
                        try {
                          await api(`/admin/sources/${encodeURIComponent(source.id)}`, {
                            method: "PATCH",
                            body: JSON.stringify({ enabled: !source.enabled }),
                          });
                          await load();
                        } catch (err) {
                          setError(
                            t("knowledge.toggleFailed", {
                              action: source.enabled ? t("knowledge.disable") : t("knowledge.enable"),
                              id: source.id,
                              reason: errorText(err),
                            }),
                          );
                        }
                      }}
                    >
                      {source.enabled ? t("knowledge.disable") : t("knowledge.enable")}
                    </Button>
                    {me?.is_super_admin ? (
                      <ConfirmDialog
                        danger
                        title={t("common.deleteTitle", { name: source.id })}
                        confirmText={source.id}
                        onConfirm={async () => {
                          const path = `/admin/sources/${encodeURIComponent(source.id)}`;
                          // A global source's documents stay live in the corpus unless they are
                          // retired with it, and the gateway will not decide that silently: it
                          // answers 409 until a choice is made. Ask here, with the count.
                          const { count } = await api<{ count: number }>(`${path}/documents`);
                          let choice = "";
                          if (count > 0) {
                            choice = window.confirm(t("knowledge.retireDocsPrompt", { count }))
                              ? "?documents=retire"
                              : "?documents=keep";
                          }
                          await api(`${path}${choice}`, { method: "DELETE" });
                          await load();
                        }}
                      >
                        {t("common.delete")}
                      </ConfirmDialog>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!loaded ? <TableSkeleton cols={4} /> : null}
            {loaded && sources.length === 0 && !error ? <TableEmpty colSpan={4}>{t("knowledge.emptySources")}</TableEmpty> : null}
          </TableBody>
        </Table>
        {me?.is_super_admin ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <Field>
              {t("knowledge.colId")}
              {/* Same rule as the gateway's SourceCreateRequest: the id is a URL path segment. */}
              <Input
                value={id}
                onChange={(e) => setId(e.target.value)}
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
                maxLength={128}
                title={t("knowledge.idHint")}
              />
            </Field>
            <Field className="min-w-[240px] flex-1">
              {t("knowledge.fieldUri")}
              <Input value={uri} onChange={(e) => setUri(e.target.value)} />
            </Field>
            <ConfirmDialog
              size="md"
              title={t("knowledge.registerTitle")}
              confirmText={id || t("knowledge.registerWord")}
              onConfirm={async () => {
                await api("/admin/sources", {
                  method: "POST",
                  body: JSON.stringify({ id, type: "web", uri }),
                });
                setId("");
                await load();
              }}
            >
              {t("knowledge.register")}
            </ConfirmDialog>
          </div>
        ) : null}
      </Card>
      <Card className="mt-4">
        <CardHeader>
          <div>
            <CardTitle>{t("knowledge.indexes")}</CardTitle>
            <CardDescription>{t("knowledge.indexesHelp")}</CardDescription>
          </div>
          <ConfirmDialog
            title={t("knowledge.rebuildTitle")}
            confirmText={t("knowledge.rebuildWord")}
            onConfirm={async () => {
              await api("/admin/index/rebuild", { method: "POST" });
              await load();
            }}
          >
            {t("knowledge.rebuild")}
          </ConfirmDialog>
        </CardHeader>
        <Table flush>
          <TableHeader>
            <TableRow>
              <TableHead>{t("knowledge.colPhysicalName")}</TableHead>
              <TableHead>{t("common.active")}</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {indexes.map((row) => (
              <TableRow key={row.physical_name}>
                <TableCell>
                  <code className="text-xs">{row.physical_name}</code>
                </TableCell>
                <TableCell>
                  {row.active ? (
                    <Badge tone="ok" dot pulse>
                      {t("common.yes")}
                    </Badge>
                  ) : (
                    ""
                  )}
                </TableCell>
                <TableCell className="text-end">
                  {row.active ? null : (
                    <ConfirmDialog
                      title={t("knowledge.rollbackAliasTitle")}
                      confirmText={t("common.rollbackWord")}
                      onConfirm={async () => {
                        await api("/admin/index/rollback", {
                          method: "POST",
                          body: JSON.stringify({ physical_name: row.physical_name }),
                        });
                        await load();
                      }}
                    >
                      {t("common.rollback")}
                    </ConfirmDialog>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!loaded ? <TableSkeleton cols={3} rows={2} /> : null}
            {loaded && indexes.length === 0 && !error ? <TableEmpty colSpan={3}>{t("knowledge.emptyIndexes")}</TableEmpty> : null}
          </TableBody>
        </Table>
      </Card>
    </>
  );
}
