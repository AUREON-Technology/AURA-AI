"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { CircleCheck, Download, RefreshCw, Sparkles, Users, PenLine } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric, MetricGrid } from "@/components/ui/metric";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { useI18n } from "@/lib/i18n/context";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Recommendation = {
  recommended?: boolean;
  reasons?: string[];
  dataset_digest?: string;
  estimated_examples?: number;
  estimated_tokens?: number;
  estimated_cost_usd?: number | null;
  estimated_train_hours_cpu?: number | null;
  base_model?: string;
  method?: string;
  statistics?: {
    total_candidates?: number;
    accepted?: number;
    acceptance_rate?: number;
    distinct_users?: number;
    corrected_fraction?: number;
  };
  dataset?: { digest?: string; example_count?: number };
};

type Job = {
  id: string;
  status: string;
  dataset_digest: string;
  artefact_uri?: string;
  error?: string | null;
};

export default function LearningPage() {
  const { t, errorText } = useI18n();
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    const [rec, jobList] = await Promise.all([
      api<{ recommendation: Recommendation }>("/admin/learning/recommendation"),
      api<{ jobs: Job[] }>("/admin/learning/jobs").catch(() => ({ jobs: [] as Job[] })),
    ]);
    setRecommendation(rec.recommendation);
    setJobs(jobList.jobs ?? []);
  }

  useEffect(() => {
    load()
      .catch((err: unknown) => setError(err))
      .finally(() => setLoaded(true));
  }, []);

  const stats = recommendation?.statistics;

  const loading = !loaded;

  return (
    <>
      <PageHeader title={t("nav.learning")}>{t("learning.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}

      <MetricGrid>
        <Metric index={0} icon={Sparkles} label={t("learning.candidates")} loading={loading} value={stats?.total_candidates ?? 0} />
        <Metric index={1} icon={CircleCheck} label={t("learning.accepted")} loading={loading} value={stats?.accepted ?? 0} />
        <Metric index={2} icon={Users} label={t("learning.distinctUsers")} loading={loading} value={stats?.distinct_users ?? 0} />
        <Metric
          index={3}
          icon={PenLine}
          label={t("learning.corrected")}
          loading={loading}
          value={`${Math.round((stats?.corrected_fraction ?? 0) * 100)}%`}
        />
      </MetricGrid>

      <Card className="mt-4 motion-safe:animate-fade-up [animation-delay:280ms]">
        <CardHeader className="mb-2">
          <CardTitle>
            {t("learning.recommendation")}{" "}
            {recommendation?.recommended ? (
              <Badge tone="ok" dot pulse>
                {t("learning.recommended")}
              </Badge>
            ) : (
              <Badge dot>{t("learning.notYet")}</Badge>
            )}
          </CardTitle>
        </CardHeader>
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="w-3/4" />
            <Skeleton className="w-1/2" />
          </div>
        ) : (
          <>
            <dl className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted">
              <div>
                <dt className="inline">{t("common.digest")}: </dt>
                <dd className="inline font-mono text-xs text-fg">{recommendation?.dataset_digest || "—"}</dd>
              </div>
              <div>
                <dt className="inline">{t("learning.examples")}: </dt>
                <dd className="inline text-fg tabular-nums">{recommendation?.estimated_examples ?? 0}</dd>
              </div>
              <div>
                <dt className="inline">{t("learning.tokens")}: </dt>
                <dd className="inline text-fg tabular-nums">{recommendation?.estimated_tokens ?? 0}</dd>
              </div>
              {recommendation?.estimated_train_hours_cpu != null ? (
                <div>
                  <dt className="inline">{t("learning.cpuHoursLabel")} ≈ </dt>
                  <dd className="inline text-fg tabular-nums">{recommendation.estimated_train_hours_cpu}</dd>
                </div>
              ) : null}
            </dl>
            {(recommendation?.reasons ?? []).length > 0 ? (
              <ul className="mt-3 list-disc space-y-1 ps-5 text-[13.5px]">
                {(recommendation?.reasons ?? []).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : null}
          </>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await load();
              } catch (err) {
                setError(err);
              } finally {
                setBusy(false);
              }
            }}
          >
            <RefreshCw aria-hidden="true" className={cn(busy && "motion-safe:animate-spin")} />
            {t("common.refresh")}
          </Button>
          <ConfirmDialog
            title={t("learning.requestTitle")}
            confirmText={t("common.requestWord")}
            disabled={!recommendation?.dataset_digest}
            onConfirm={async () => {
              const body = await api<{ approval: { id: string } }>(
                "/admin/learning/request-approval",
                { method: "POST", body: JSON.stringify({}) },
              );
              setApprovalId(body.approval.id);
              await load();
            }}
          >
            {t("learning.requestApproval")}
          </ConfirmDialog>
          {recommendation?.dataset_digest ? (
            <a
              className={buttonVariants({ size: "sm" })}
              href={`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/v3/admin/learning/datasets/${recommendation.dataset_digest}?format=chatml`}
            >
              <Download aria-hidden="true" />
              {t("learning.exportChatml")}
            </a>
          ) : null}
        </div>
        {!loading && !recommendation?.dataset_digest ? (
          <p className="mt-3 text-[13px] text-muted">{t("learning.noDataset")}</p>
        ) : null}
        {approvalId ? <p className="mt-2 text-[13px] text-muted">{t("learning.pendingApproval", { id: approvalId })}</p> : null}
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{t("learning.jobs")}</CardTitle>
        </CardHeader>
        <Table flush>
          <TableHeader>
            <TableRow>
              <TableHead>{t("learning.colJob")}</TableHead>
              <TableHead>{t("common.status")}</TableHead>
              <TableHead>{t("common.digest")}</TableHead>
              <TableHead>{t("common.artefact")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell className="font-medium">{job.id}</TableCell>
                <TableCell>
                  <Badge
                    dot
                    pulse={job.status === "running"}
                    tone={job.status === "succeeded" ? "ok" : job.status === "failed" ? "bad" : job.status === "running" ? "warn" : "neutral"}
                  >
                    {job.status}
                  </Badge>
                </TableCell>
                <TableCell>
                  <code className="text-xs">{job.dataset_digest}</code>
                </TableCell>
                <TableCell className="text-muted">{job.artefact_uri || job.error || "—"}</TableCell>
              </TableRow>
            ))}
            {!loaded ? <TableSkeleton cols={4} rows={2} /> : null}
            {loaded && jobs.length === 0 && !error ? <TableEmpty colSpan={4}>{t("learning.emptyJobs")}</TableEmpty> : null}
          </TableBody>
        </Table>
      </Card>
    </>
  );
}
