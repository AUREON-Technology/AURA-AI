"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

type Metrics = {
  total_records?: number;
  average_rating?: number;
  corrected_percentage?: number;
  model_distributions?: Record<string, number>;
};

export default function LearningPage() {
  const [metrics, setMetrics] = useState<Metrics>({});
  const [taskId, setTaskId] = useState<string | null>(null);
  const [job, setJob] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Metrics>("/learning/evaluate")
      .then(setMetrics)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Learning</h1>
      <p className="sub">Aggregated feedback only. Training still requires a recorded approval.</p>
      {error ? <div className="banner">{error}</div> : null}
      <div className="grid">
        <div className="card metric">
          Records
          <b>{metrics.total_records ?? 0}</b>
        </div>
        <div className="card metric">
          Average rating
          <b>{metrics.average_rating ?? "—"}</b>
        </div>
        <div className="card metric">
          Corrected
          <b>{metrics.corrected_percentage ?? 0}%</b>
        </div>
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        <a className="btn" href={`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/v3/learning/export?format=alpaca`}>
          Export Alpaca
        </a>
        <button
          className="btn primary"
          type="button"
          onClick={async () => {
            const body = await api<{ task_id: string }>("/learning/build_dataset", { method: "POST" });
            setTaskId(body.task_id);
          }}
        >
          Queue dataset build
        </button>
      </div>
      {taskId ? (
        <p className="muted">
          Task {taskId}{" "}
          <button
            className="btn"
            type="button"
            onClick={async () => {
              const body = await api<{ state: string }>(`/admin/jobs/${taskId}`);
              setJob(body.state);
            }}
          >
            Poll
          </button>{" "}
          {job}
        </p>
      ) : null}
    </>
  );
}
