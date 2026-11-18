"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { useI18n } from "@/lib/i18n/context";
import { api } from "@/lib/api";
import type { AdminStatus, PinResult, PromptRow } from "@/lib/types";

export default function PromptsPage() {
  const { t, errorText } = useI18n();
  const [rows, setRows] = useState<PromptRow[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loaded, setLoaded] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => setRows(status.prompts ?? []))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoaded(true));
  }

  useEffect(load, []);

  return (
    <>
      <PageHeader title={t("nav.prompts")}>{t("prompts.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      {notice ? (
        <p className="mb-4 text-[13px] text-muted motion-safe:animate-fade-up" data-testid="prompt-notice">
          {notice}
        </p>
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("prompts.colTemplate")}</TableHead>
            <TableHead>{t("common.active")}</TableHead>
            <TableHead>{t("prompts.colPinned")}</TableHead>
            <TableHead>{t("prompts.colChecksum")}</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell className="font-medium">{row.id}</TableCell>
              <TableCell>{row.active}</TableCell>
              <TableCell>{row.pinned ? <Badge tone="brand">{row.pinned}</Badge> : "—"}</TableCell>
              <TableCell>
                <code className="text-xs text-muted">{row.checksum}</code>
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap justify-end gap-1.5">
                  {(row.versions ?? []).map((version) => (
                    <ConfirmDialog
                      key={version}
                      title={t("prompts.pinTitle", { target: `${row.id}@${version}` })}
                      confirmText={version}
                      onConfirm={async () => {
                        const result = await api<PinResult>(
                          `/admin/prompts/${encodeURIComponent(row.id)}/pin`,
                          { method: "POST", body: JSON.stringify({ version }) },
                        );
                        setNotice(
                          result?.shared
                            ? t("prompts.pinnedShared", {
                                target: result.pinned,
                                seconds: result.propagation_s ?? 5,
                              })
                            : t("prompts.pinned", { target: result?.pinned ?? `${row.id}@${version}` }),
                        );
                        load();
                      }}
                    >
                      {t("prompts.pin", { version })}
                    </ConfirmDialog>
                  ))}
                  {row.pinned ? (
                    <ConfirmDialog
                      danger
                      title={t("prompts.unpinTitle", { id: row.id })}
                      confirmText={t("prompts.unpinWord")}
                      onConfirm={async () => {
                        await api(`/admin/prompts/${encodeURIComponent(row.id)}/pin`, { method: "DELETE" });
                        setNotice(t("prompts.unpinned", { id: row.id }));
                        load();
                      }}
                    >
                      {t("prompts.unpin")}
                    </ConfirmDialog>
                  ) : null}
                </div>
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={5} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={5}>{t("prompts.empty")}</TableEmpty> : null}
        </TableBody>
      </Table>
    </>
  );
}
