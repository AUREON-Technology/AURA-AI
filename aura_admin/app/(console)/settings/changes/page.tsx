"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { ChangeRow } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

const TONE: Record<ChangeRow["status"], "neutral" | "ok" | "warn" | "bad"> = {
  recorded: "neutral",
  watching: "warn",
  succeeded: "ok",
  rolling_back: "warn",
  rolled_back: "bad",
  failed: "bad",
};

const PLATFORM_ACTIONS = new Set(["connect", "replace_token", "disconnect"]);
const SERVICE_ACTIONS = new Set([
  "set_variables", "redeploy", "restart", "rollback",
  "space_variables", "space_secrets", "space_pause", "space_restart", "space_rebuild", "space_hardware", "space_sleep_time",
  "replace_key", "rotate_key", "revoke_key",
  "add_cloudflare_provider", "replace_cloudflare_provider", "remove_cloudflare_provider",
  "grafana_apply", "vector_snapshot", "vector_restore",
]);

export default function ChangesPage() {
  const { t, lang, errorText } = useI18n();
  const [rows, setRows] = useState<ChangeRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api<{ changes: ChangeRow[] }>("/admin/control/changes?limit=100")
      .then((body) => setRows(body.changes))
      .catch((err: unknown) => setError(err));
  }, []);

  function what(row: ChangeRow): string {
    const platform = t(`settings.platform.${row.platform}` as MessageKey);
    if (PLATFORM_ACTIONS.has(row.action)) {
      return t(`settings.changes.action.${row.action}` as MessageKey, { platform });
    }
    if (SERVICE_ACTIONS.has(row.action)) {
      const service =
        typeof row.detail.slot === "string"
          ? row.detail.slot
          : typeof row.detail.provider === "string"
            ? row.detail.provider
            : typeof row.detail.service === "string"
              ? row.detail.service
              : row.target;
      const variables = Array.isArray(row.detail.variables)
        ? (row.detail.variables as { name?: string }[]).map((v) => v.name).filter(Boolean).join(", ")
        : "";
      const text = t(`settings.changes.action.${row.action}` as MessageKey, { service });
      return variables ? `${text}: ${variables}` : text;
    }
    return `${row.action} · ${row.target}`;
  }

  return (
    <>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.changes.when")}</TableHead>
              <TableHead>{t("settings.changes.who")}</TableHead>
              <TableHead>{t("settings.changes.what")}</TableHead>
              <TableHead>{t("common.status")}</TableHead>
              <TableHead>{t("settings.changes.reason")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows === null && !error ? <TableSkeleton cols={5} /> : null}
            {rows?.length === 0 ? <TableEmpty colSpan={5}>{t("settings.changes.empty")}</TableEmpty> : null}
            {rows?.map((row) => (
              <TableRow key={row.change_id} data-testid="change-row">
                <TableCell className="whitespace-nowrap">
                  {new Date(row.created_at).toLocaleString(lang === "ar" ? "ar" : "en")}
                </TableCell>
                <TableCell className="break-all">{row.actor}</TableCell>
                <TableCell>{what(row)}</TableCell>
                <TableCell>
                  <Badge tone={TONE[row.status]}>{t(`settings.changes.status.${row.status}` as MessageKey)}</Badge>
                </TableCell>
                <TableCell className="text-muted">{row.reason || "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </>
  );
}
