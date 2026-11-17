"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { KnowledgeSource, Me } from "@/lib/types";

export default function KnowledgePage() {
  const { t, errorText } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [indexes, setIndexes] = useState<{ physical_name: string; active: boolean }[]>([]);
  const [id, setId] = useState("");
  const [uri, setUri] = useState("https://");
  const [error, setError] = useState<unknown>(null);

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
    load().catch((err: unknown) => setError(err));
  }, []);

  return (
    <>
      <h1>{t("nav.knowledge")}</h1>
      <p className="sub">{t("knowledge.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      <div className="card">
        <h2>{t("knowledge.sources")}</h2>
        <table className="table">
          <thead>
            <tr>
              <th>{t("knowledge.colId")}</th>
              <th>{t("knowledge.colUri")}</th>
              <th>{t("knowledge.colEnabled")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => (
              <tr key={source.id}>
                <td>{source.id}</td>
                <td>{source.uri}</td>
                <td>{source.enabled ? t("common.yes") : t("common.no")}</td>
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
                  </button>
                  {me?.is_super_admin ? (
                    <ConfirmDialog
                      danger
                      title={t("common.deleteTitle", { name: source.id })}
                      confirmText={source.id}
                      onConfirm={async () => {
                        await api(`/admin/sources/${encodeURIComponent(source.id)}`, { method: "DELETE" });
                        await load();
                      }}
                    >
                      {t("common.delete")}
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
              {t("knowledge.colId")}
              {/* Same rule as the gateway's SourceCreateRequest: the id is a URL path segment. */}
              <input
                value={id}
                onChange={(e) => setId(e.target.value)}
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
                maxLength={128}
                title={t("knowledge.idHint")}
              />
            </label>
            <label className="field">
              {t("knowledge.fieldUri")}
              <input value={uri} onChange={(e) => setUri(e.target.value)} />
            </label>
            <ConfirmDialog
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
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>{t("knowledge.indexes")}</h2>
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
        <table className="table">
          <thead>
            <tr>
              <th>{t("knowledge.colPhysicalName")}</th>
              <th>{t("common.active")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {indexes.map((row) => (
              <tr key={row.physical_name}>
                <td>{row.physical_name}</td>
                <td>{row.active ? t("common.yes") : ""}</td>
                <td>
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
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
