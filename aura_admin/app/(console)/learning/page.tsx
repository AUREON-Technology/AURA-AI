"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api } from "@/lib/api";

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
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
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
    load().catch((err: Error) => setError(err.message));
  }, []);

  const stats = recommendation?.statistics;

  return (
    <>
      <h1>Learning</h1>
      <p className="sub">
        Loop B curation from live feedback. Training still requires a recorded approval bound to
        the dataset digest.
      </p>
      {error ? <div className="banner">{error}</div> : null}

      <div className="grid">
        <div className="card metric">
          Candidates
          <b>{stats?.total_candidates ?? 0}</b>
        </div>
        <div className="card metric">
          Accepted
          <b>{stats?.accepted ?? 0}</b>
        </div>
        <div className="card metric">
          Distinct users
          <b>{stats?.distinct_users ?? 0}</b>
        </div>
        <div className="card metric">
          Corrected
          <b>{Math.round((stats?.corrected_fraction ?? 0) * 100)}%</b>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>
          Recommendation{" "}
          {recommendation?.recommended ? (
            <span className="pill ok">recommended</span>
          ) : (
            <span className="pill">not yet</span>
          )}
        </h2>
        <p className="muted">
          Digest: {recommendation?.dataset_digest || "—"} · Examples:{" "}
          {recommendation?.estimated_examples ?? 0} · Tokens:{" "}
          {recommendation?.estimated_tokens ?? 0}
          {recommendation?.estimated_train_hours_cpu != null
            ? ` · CPU hours ≈ ${recommendation.estimated_train_hours_cpu}`
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
                setError((err as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Refresh
          </button>
          <ConfirmDialog
            title="Request fine-tune approval"
            confirmText="request"
            onConfirm={async () => {
              const body = await api<{ approval: { id: string } }>(
                "/admin/learning/request-approval",
                { method: "POST", body: JSON.stringify({}) },
              );
              setApprovalId(body.approval.id);
              await load();
            }}
          >
            Request approval
          </ConfirmDialog>
          {recommendation?.dataset_digest ? (
            <a
              className="btn"
              href={`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/v3/admin/learning/datasets/${recommendation.dataset_digest}?format=chatml`}
            >
              Export ChatML
            </a>
          ) : null}
        </div>
        {approvalId ? <p className="muted">Pending approval: {approvalId}</p> : null}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Fine-tune jobs</h2>
        <table className="table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Status</th>
              <th>Digest</th>
              <th>Artefact</th>
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
