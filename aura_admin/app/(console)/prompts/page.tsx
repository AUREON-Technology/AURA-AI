"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { AdminStatus, PinResult, PromptRow } from "@/lib/types";

export default function PromptsPage() {
  const [rows, setRows] = useState<PromptRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api<AdminStatus>("/admin/status")
      .then((status) => setRows(status.prompts ?? []))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }

  useEffect(load, []);

  return (
    <>
      <PageHeader title="Prompts">Pin a previous version to roll back a bad change. No deploy required.</PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      {notice ? (
        <p className="mb-4 text-[13px] text-muted motion-safe:animate-fade-up" data-testid="prompt-notice">
          {notice}
        </p>
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Template</TableHead>
            <TableHead>Active</TableHead>
            <TableHead>Pinned</TableHead>
            <TableHead>Checksum</TableHead>
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
                      title={`Pin ${row.id}@${version}`}
                      confirmText={version}
                      onConfirm={async () => {
                        const result = await api<PinResult>(
                          `/admin/prompts/${encodeURIComponent(row.id)}/pin`,
                          { method: "POST", body: JSON.stringify({ version }) },
                        );
                        setNotice(
                          result?.shared
                            ? `Pinned ${result.pinned}; applies to all workers within ~${result.propagation_s ?? 5} s.`
                            : `Pinned ${result?.pinned ?? `${row.id}@${version}`}.`,
                        );
                        load();
                      }}
                    >
                      Pin {version}
                    </ConfirmDialog>
                  ))}
                  {row.pinned ? (
                    <ConfirmDialog
                      danger
                      title={`Unpin ${row.id}`}
                      confirmText="unpin"
                      onConfirm={async () => {
                        await api(`/admin/prompts/${encodeURIComponent(row.id)}/pin`, { method: "DELETE" });
                        setNotice(`Released the pin on ${row.id}; the active version applies again.`);
                        load();
                      }}
                    >
                      Unpin
                    </ConfirmDialog>
                  ) : null}
                </div>
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={5} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={5}>No prompt templates registered.</TableEmpty> : null}
        </TableBody>
      </Table>
    </>
  );
}
