"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Camera, History } from "lucide-react";

import { stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { AuraApiError, api } from "@/lib/api";
import type { ChangeRow, VectorOverview, VectorSnapshotRow } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

function size(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Snapshots({ collection, onDone }: { collection: string; onDone: (message: string) => void }) {
  const { t, lang, errorText } = useI18n();
  const stepUp = useStepUp();
  const [rows, setRows] = useState<VectorSnapshotRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api<{ snapshots: VectorSnapshotRow[] }>(`/admin/control/vector/collections/${collection}/snapshots`)
      .then((body) => {
        setRows(body.snapshots);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, [collection]);

  useEffect(load, [load]);

  return (
    <Card className="flex flex-col gap-3 p-4" data-testid="snapshots">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[14px] font-semibold">{t("settings.vector.snapshotsOf", { name: collection })}</h3>
        <ConfirmDialog
          title={t("settings.vector.snapshotTitle", { name: collection })}
          confirmText={collection}
          onConfirm={async () => {
            try {
              await stepUp.write<ChangeRow>(`/admin/control/vector/collections/${collection}/snapshots`, { confirm: collection });
              onDone(t("settings.vector.snapshotMade", { name: collection }));
              load();
            } catch (err) {
              throw stepUpError(err, t);
            }
          }}
        >
          <Camera aria-hidden="true" />{t("settings.vector.snapshot")}
        </ConfirmDialog>
      </div>
      {error ? <Alert className="mb-0">{errorText(error)}</Alert> : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("settings.railway.name")}</TableHead>
            <TableHead>{t("settings.vector.created")}</TableHead>
            <TableHead>{t("settings.vector.size")}</TableHead>
            <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows === null && !error ? <TableSkeleton cols={4} /> : null}
          {rows?.length === 0 ? <TableEmpty colSpan={4}>{t("settings.vector.noSnapshots")}</TableEmpty> : null}
          {rows?.map((s) => (
            <TableRow key={s.name} data-testid="snapshot-row">
              <TableCell><code dir="ltr" className="break-all text-[12px]">{s.name}</code></TableCell>
              <TableCell className="whitespace-nowrap">{s.created_at ? new Date(s.created_at).toLocaleString(lang === "ar" ? "ar" : "en") : "—"}</TableCell>
              <TableCell>{size(s.size)}</TableCell>
              <TableCell className="text-end">
                <ConfirmDialog
                  title={t("settings.vector.restoreTitle", { name: collection })}
                  confirmText={collection}
                  danger
                  label={t("settings.vector.restoreFrom", { snapshot: s.name })}
                  onConfirm={async () => {
                    try {
                      const result = await stepUp.write<ChangeRow & { safety_snapshot: string }>(
                        `/admin/control/vector/collections/${collection}/restore`,
                        { snapshot: s.name, confirm: collection },
                      );
                      onDone(t("settings.vector.restored", { name: collection, safety: result.safety_snapshot }));
                      load();
                    } catch (err) {
                      throw stepUpError(err, t);
                    }
                  }}
                  body={<p className="text-[13px] text-warn">{t("settings.vector.restoreBody", { snapshot: s.name })}</p>}
                >
                  <History aria-hidden="true" />{t("settings.vector.restore")}
                </ConfirmDialog>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

export default function VectorPage() {
  const { t, errorText } = useI18n();
  const [overview, setOverview] = useState<VectorOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<VectorOverview>("/admin/control/vector")
      .then((body) => {
        setOverview(body);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  if (error && !overview) {
    const notConnected = error instanceof AuraApiError && error.status === 404;
    return notConnected ? (
      <Alert data-testid="vector-not-connected">
        {t("settings.vector.notConnected")}{" "}
        <Link className="underline" href="/settings">{t("settings.tab.overview")}</Link>
      </Alert>
    ) : (
      <Alert>{errorText(error)}</Alert>
    );
  }
  if (!overview) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="flex flex-col gap-4" data-testid="vector-panel">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
        <code dir="ltr" className="break-all">{overview.cluster}</code>
        <Badge tone={overview.healthy ? "ok" : "bad"} dot data-testid="vector-health">
          {overview.healthy ? t("settings.keys.healthy") : t("settings.vector.unreachable")}
        </Badge>
      </div>
      <p className="text-[13px] text-muted">{t("settings.vector.note")}</p>
      {notice ? <p className="text-[13px] text-ok" role="status" data-testid="vector-notice">{notice}</p> : null}
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.vector.collection")}</TableHead>
              <TableHead>{t("common.status")}</TableHead>
              <TableHead>{t("settings.vector.points")}</TableHead>
              <TableHead>{t("settings.vector.vectors")}</TableHead>
              <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {overview.collections.length === 0 ? <TableEmpty colSpan={5}>{t("settings.vector.noCollections")}</TableEmpty> : null}
            {overview.collections.map((c) => (
              <TableRow key={c.name} data-testid={`collection-${c.name}`}>
                <TableCell><code dir="ltr" className="text-[12.5px] font-semibold">{c.name}</code></TableCell>
                <TableCell>
                  <Badge tone={c.status === "green" ? "ok" : c.status === "red" ? "bad" : "warn"}>{c.status || "—"}</Badge>
                </TableCell>
                <TableCell>{c.points_count?.toLocaleString() ?? "—"}</TableCell>
                <TableCell className="whitespace-nowrap">{c.vector_size ? `${c.vector_size} · ${c.distance}` : "—"}</TableCell>
                <TableCell className="text-end">
                  <Button variant="ghost" size="sm" aria-pressed={selected === c.name} onClick={() => setSelected(c.name)}>
                    {t("settings.vector.snapshots")}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {selected ? (
        <Snapshots
          key={selected}
          collection={selected}
          onDone={(message) => {
            setNotice(message);
            load();
          }}
        />
      ) : null}
    </div>
  );
}
