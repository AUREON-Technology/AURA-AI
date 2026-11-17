"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";

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
    load().catch((err: unknown) => setError(err));
  }, []);

  const stats = recommendation?.statistics;

  return (
    <>
      <h1>{t("nav.learning")}</h1>
      <p className="sub">{t("learning.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}

      <div className="grid">
        <div className="card metric">
          {t("learning.candidates")}
          <b>{stats?.total_candidates ?? 0}</b>
        </div>
        <div className="card metric">
          {t("learning.accepted")}
          <b>{stats?.accepted ?? 0}</b>
        </div>
        <div className="card metric">
          {t("learning.distinctUsers")}
          <b>{stats?.distinct_users ?? 0}</b>
        </div>
        <div className="card metric">
          {t("learning.corrected")}
          <b>{Math.round((stats?.corrected_fraction ?? 0) * 100)}%</b>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>
          {t("learning.recommendation")}{" "}
          {recommendation?.recommended ? (
            <span className="pill ok">{t("learning.recommended")}</span>
          ) : (
            <span className="pill">{t("learning.notYet")}</span>
          )}
        </h2>
        <p className="muted">
          {t("learning.summary", {
            digest: recommendation?.dataset_digest || "—",
            examples: recommendation?.estimated_examples ?? 0,
            tokens: recommendation?.estimated_tokens ?? 0,
          })}
          {recommendation?.estimated_train_hours_cpu != null
            ? t("learning.cpuHours", { hours: recommendation.estimated_train_hours_cpu })
            : ""}
        </p>
        <ul>
          {(recommendation?.reasons ?? []).map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn"
            type="button"
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
            {t("common.refresh")}
          </button>
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
              className="btn"
              href={`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/v3/admin/learning/datasets/${recommendation.dataset_digest}?format=chatml`}
            >
              {t("learning.exportChatml")}
            </a>
          ) : null}
        </div>
        {!recommendation?.dataset_digest ? (
          <p className="muted">{t("learning.noDataset")}</p>
        ) : null}
        {approvalId ? <p className="muted">{t("learning.pendingApproval", { id: approvalId })}</p> : null}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>{t("learning.jobs")}</h2>
        <table className="table">
          <thead>
            <tr>
              <th>{t("learning.colJob")}</th>
              <th>{t("common.status")}</th>
              <th>{t("common.digest")}</th>
              <th>{t("common.artefact")}</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.id}</td>
                <td>{job.status}</td>
                <td>{job.dataset_digest}</td>
                <td>{job.artefact_uri || job.error || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
