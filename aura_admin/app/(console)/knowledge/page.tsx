"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import type { KnowledgeSource, Me } from "@/lib/types";

export default function KnowledgePage() {
  const [me, setMe] = useState<Me | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [indexes, setIndexes] = useState<{ physical_name: string; active: boolean }[]>([]);
  const [id, setId] = useState("");
  const [uri, setUri] = useState("https://");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [meRow, sourceBody, indexBody] = await Promise.all([
      fetchMe(),
      api<{ sources: KnowledgeSource[] }>("/admin/sources"),
      api<{ indexes: { physical_name: string; active: boolean }[] }>("/admin/indexes"),
    ]);
    setMe(meRow);
    setSources(sourceBody.sources);
    setIndexes(indexBody.indexes);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Knowledge</h1>
      <p className="sub">Approved sources and versioned indexes. Not a user document browser.</p>
      {error ? <div className="banner">{error}</div> : null}
      <div className="card">
        <h2>Sources</h2>
        <table className="table">
          <thead>
            <tr>
              <th>Id</th>
              <th>URI</th>
              <th>Enabled</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => (
              <tr key={source.id}>
                <td>{source.id}</td>
                <td>{source.uri}</td>
                <td>{source.enabled ? "yes" : "no"}</td>
                <td>
                  <button
                    className="btn"
                    type="button"
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
                          `${source.enabled ? "Disable" : "Enable"} ${source.id} failed: ${
                            err instanceof Error ? err.message : "request failed"
                          }`,
                        );
                      }
                    }}
                  >
                    {source.enabled ? "Disable" : "Enable"}
                  </button>
                  {me?.is_super_admin ? (
                    <ConfirmDialog
                      danger
                      title={`Delete ${source.id}`}
                      confirmText={source.id}
                      onConfirm={async () => {
                        await api(`/admin/sources/${encodeURIComponent(source.id)}`, { method: "DELETE" });
                        await load();
                      }}
                    >
                      Delete
                    </ConfirmDialog>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {me?.is_super_admin ? (
          <div className="row" style={{ marginTop: 12 }}>
            <label className="field">
              Id
              {/* Same rule as the gateway's SourceCreateRequest: the id is a URL path segment. */}
              <input
                value={id}
                onChange={(e) => setId(e.target.value)}
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
                maxLength={128}
                title="Letters, digits, '.', '_', ':' or '-', starting with a letter or digit"
              />
            </label>
            <label className="field">
              HTTPS URI
              <input value={uri} onChange={(e) => setUri(e.target.value)} />
            </label>
            <ConfirmDialog
              title="Register source"
              confirmText={id || "source"}
              onConfirm={async () => {
                await api("/admin/sources", {
                  method: "POST",
                  body: JSON.stringify({ id, type: "web", uri }),
                });
                setId("");
                await load();
              }}
            >
              Register
            </ConfirmDialog>
          </div>
        ) : null}
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Indexes</h2>
        <ConfirmDialog
          title="Rebuild index"
          confirmText="rebuild"
          onConfirm={async () => {
            await api("/admin/index/rebuild", { method: "POST" });
            await load();
          }}
        >
          Rebuild
        </ConfirmDialog>
        <table className="table">
          <thead>
            <tr>
              <th>Physical name</th>
              <th>Active</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {indexes.map((row) => (
              <tr key={row.physical_name}>
                <td>{row.physical_name}</td>
                <td>{row.active ? "yes" : ""}</td>
                <td>
                  {row.active ? null : (
                    <ConfirmDialog
                      title="Rollback alias"
                      confirmText="rollback"
                      onConfirm={async () => {
                        await api("/admin/index/rollback", {
                          method: "POST",
                          body: JSON.stringify({ physical_name: row.physical_name }),
                        });
                        await load();
                      }}
                    >
                      Rollback
                    </ConfirmDialog>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
