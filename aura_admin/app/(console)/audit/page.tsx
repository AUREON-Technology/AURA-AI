"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";

export default function AuditPage() {
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [action, setAction] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Stable across renders (state setters are), so the mount-time effect below can list it as its
  // one dependency instead of silencing react-hooks/exhaustive-deps (audit G-10 lint gate).
  const load = useCallback((next: string) => {
    const query = next ? `?action=${encodeURIComponent(next)}` : "";
    api<{ events: AuditEvent[] }>(`/admin/audit${query}`)
      .then((body) => setRows(body.events))
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  return (
    <>
      <h1>Audit</h1>
      <p className="sub">Append-only. Arguments are digests, never verbatim user content.</p>
      {error ? <div className="banner">{error}</div> : null}
      <div className="row">
        <input value={action} onChange={(e) => setAction(e.target.value)} placeholder="action prefix" />
        <button className="btn" type="button" onClick={() => load(action)}>
          Filter
        </button>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>When</th>
            <th>Actor</th>
            <th>Action</th>
            <th>Resource</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.at}-${index}`}>
              <td>{row.at}</td>
              <td>
                {row.actor} ({row.actor_role})
              </td>
              <td>{row.action}</td>
              <td>{row.resource}</td>
              <td>{row.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
