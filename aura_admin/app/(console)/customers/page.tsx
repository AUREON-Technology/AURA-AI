"use client";

import { useEffect, useId, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { Customer, CustomersPage } from "@/lib/types";

// SaaS P1-F3 (spec §5.4 "Owner tooling"): read-only customers plus one audited write. Both
// routes are SuperAdmin-only (aura_core/api/billing.py), so nobody else is sent to them.
const PAGE_SIZE = 50;
// BillingService.extend_grace refuses anything else with 409; a store owns its own subscriptions.
const BEHIND = new Set(["past_due", "grace", "expired"]);

function statusTone(status: string) {
  if (status === "active" || status === "trialing") return "ok" as const;
  if (status === "past_due" || status === "grace") return "warn" as const;
  if (status === "expired" || status === "canceled") return "bad" as const;
  return "neutral" as const;
}

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

function egp(piastres: number): string {
  return `EGP ${(piastres / 100).toFixed(2)}`;
}

function GraceAction({ row, onDone }: { row: Customer; onDone: (graceUntil: string) => void }) {
  const { t } = useI18n();
  const [days, setDays] = useState("7");
  const daysId = useId();
  const name = row.workspace_name || row.workspace_id;
  return (
    <ConfirmDialog
      title={t("customers.extendTitle", { name })}
      confirmText={t("customers.extendWord")}
      body={
        <div className="flex flex-col gap-1.5">
          <label htmlFor={daysId} className="text-[12.5px] font-medium text-muted">
            {t("customers.days")}
          </label>
          <Input
            id={daysId}
            // No native min/max/step: the browser would block the submit with its own untranslated
            // bubble; the range check in onConfirm says it in the console's language instead.
            type="number"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
          <p className="text-[12px] text-muted">{t("customers.daysHelp")}</p>
        </div>
      }
      onConfirm={async () => {
        const value = Number(days);
        if (!/^\d+$/.test(days.trim()) || value < 1 || value > 30) {
          throw new Error(t("customers.daysRange"));
        }
        const result = await api<{ status: string; grace_until: string }>(
          `/admin/customers/${encodeURIComponent(row.workspace_id)}/extend-grace`,
          { method: "POST", body: JSON.stringify({ days: value }) },
        );
        onDone(result.grace_until);
      }}
    >
      {t("customers.extend")}
    </ConfirmDialog>
  );
}

export default function CustomersPage() {
  const { t, errorText } = useI18n();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [rows, setRows] = useState<Customer[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  function load(after: string | null) {
    setLoading(true);
    setError(null);
    const query = `?limit=${PAGE_SIZE}${after ? `&after=${encodeURIComponent(after)}` : ""}`;
    api<CustomersPage>(`/admin/customers${query}`)
      .then((body) => {
        // Appended by id, so a repeated "Load more" can never show a row twice.
        setRows((current) => {
          const seen = new Set(current.map((row) => row.workspace_id));
          return [...current, ...body.customers.filter((row) => !seen.has(row.workspace_id))];
        });
        setNext(body.next);
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    fetchMe()
      .then((me) => {
        setAllowed(me.is_super_admin);
        if (me.is_super_admin) load(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  function graceExtended(workspaceId: string, graceUntil: string) {
    setRows((current) =>
      current.map((row) =>
        row.workspace_id === workspaceId ? { ...row, status: "grace", grace_until: graceUntil } : row,
      ),
    );
  }

  return (
    <>
      <PageHeader title={t("nav.customers")}>{t("customers.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      {allowed === false ? (
        <Alert>{t("customers.superAdminOnly")}</Alert>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("customers.colWorkspace")}</TableHead>
                <TableHead>{t("customers.colPlan")}</TableHead>
                <TableHead>{t("common.status")}</TableHead>
                <TableHead>{t("customers.colProvider")}</TableHead>
                <TableHead>{t("customers.colUntil")}</TableHead>
                <TableHead>{t("customers.colLastPayment")}</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.workspace_id}>
                  <TableCell>
                    <div className="font-medium">{row.workspace_name || row.workspace_id}</div>
                    <div className="text-[12px] text-muted">
                      {row.kind ?? "—"} · <code className="text-xs">{row.workspace_id}</code>
                    </div>
                  </TableCell>
                  <TableCell>
                    {row.plan} × {row.seats}
                  </TableCell>
                  <TableCell>
                    <Badge tone={statusTone(row.status)} dot>
                      {row.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted">{row.provider}</TableCell>
                  <TableCell>
                    <div>{day(row.period_end)}</div>
                    {row.grace_until ? (
                      <div className="text-[12px] text-muted">
                        {t("customers.graceUntil", { date: day(row.grace_until) })}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    {row.last_payment ? (
                      <>
                        <div>{egp(row.last_payment.amount_piastres)}</div>
                        <div className="text-[12px] text-muted">
                          {row.last_payment.status} · {day(row.last_payment.at)}
                        </div>
                      </>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    {row.provider === "fawry" && BEHIND.has(row.status) ? (
                      <div className="flex justify-end">
                        <GraceAction row={row} onDone={(until) => graceExtended(row.workspace_id, until)} />
                      </div>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
              {allowed === null || (loading && rows.length === 0) ? <TableSkeleton cols={7} /> : null}
              {allowed && !loading && rows.length === 0 && !error ? (
                <TableEmpty colSpan={7}>{t("customers.empty")}</TableEmpty>
              ) : null}
            </TableBody>
          </Table>
          {next ? (
            <div className="mt-4 flex justify-center">
              <Button disabled={loading} onClick={() => load(next)}>
                {t("customers.loadMore")}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}
