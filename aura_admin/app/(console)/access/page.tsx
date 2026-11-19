"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api, fetchMe } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { ForgetResult, Me, Operator, OperatorRole } from "@/lib/types";

function roleTone(role: string) {
  if (role === "SuperAdmin") return "brand" as const;
  if (role === "Admin") return "ok" as const;
  if (role === "Developer") return "warn" as const;
  return "neutral" as const;
}

export default function AccessPage() {
  const { t, errorText } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Operator[]>([]);
  const [forgetSubject, setForgetSubject] = useState("");
  const [grantEmail, setGrantEmail] = useState("");
  const [grantRole, setGrantRole] = useState<OperatorRole>("Developer");
  const [forgetResult, setForgetResult] = useState<ForgetResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    const [who, body] = await Promise.all([
      fetchMe(),
      api<{ operators: Operator[] }>("/admin/operators"),
    ]);
    setMe(who);
    setRows(body.operators);
  }

  useEffect(() => {
    load()
      .catch((err: unknown) => setError(err))
      .finally(() => setLoaded(true));
  }, []);

  return (
    <>
      <PageHeader title={t("nav.access")}>{t("access.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      {me?.is_super_admin ? (
        <Card className="mb-4">
          <CardHeader>
            <div>
              <CardTitle>{t("access.grantHeading")}</CardTitle>
              <CardDescription>{t("access.grantHelp")}</CardDescription>
            </div>
          </CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-full max-w-xs"
              value={grantEmail}
              onChange={(e) => setGrantEmail(e.target.value)}
              placeholder={t("access.emailPlaceholder")}
              aria-label={t("access.colEmail")}
            />
            <Select
              className="w-auto"
              aria-label={t("access.colRole")}
              value={grantRole}
              onChange={(e) => setGrantRole(e.target.value as OperatorRole)}
            >
              {(["Developer", "Admin", "SuperAdmin"] as OperatorRole[]).map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </Select>
            <ConfirmDialog
              size="md"
              title={t("access.setRoleTitle", { who: grantEmail || t("access.thisUser"), role: grantRole })}
              confirmText={grantRole}
              disabled={!grantEmail.trim()}
              onConfirm={async () => {
                await api(`/admin/operators/${encodeURIComponent(grantEmail.trim())}/role`, {
                  method: "PATCH",
                  body: JSON.stringify({ role: grantRole }),
                });
                setGrantEmail("");
                await load();
              }}
            >
              {t("access.grantRole")}
            </ConfirmDialog>
          </div>
        </Card>
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("access.colEmail")}</TableHead>
            <TableHead>{t("access.colRole")}</TableHead>
            <TableHead>{t("access.colGrantedBy")}</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.email}>
              <TableCell className="font-medium">{row.email}</TableCell>
              <TableCell>
                <Badge tone={roleTone(row.role)}>{row.role}</Badge>
              </TableCell>
              <TableCell className="text-muted">{row.granted_by ?? ""}</TableCell>
              <TableCell>
                {me?.is_super_admin ? (
                  <div className="flex flex-wrap justify-end gap-1.5">
                    {(["Developer", "Admin", "SuperAdmin"] as OperatorRole[]).map((role) => (
                      <ConfirmDialog
                        key={role}
                        title={t("access.setRoleTitle", { who: row.email, role })}
                        confirmText={role}
                        onConfirm={async () => {
                          await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                            method: "PATCH",
                            body: JSON.stringify({ role }),
                          });
                          await load();
                        }}
                      >
                        {t("access.make", { role })}
                      </ConfirmDialog>
                    ))}
                    <ConfirmDialog
                      danger
                      title={t("access.removeTitle", { email: row.email })}
                      confirmText="User"
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                          method: "PATCH",
                          body: JSON.stringify({ role: "User" }),
                        });
                        await load();
                      }}
                    >
                      {t("access.removeAccess")}
                    </ConfirmDialog>
                    <ConfirmDialog
                      danger
                      title={t("access.revokeSessions")}
                      confirmText={t("access.revokeWord")}
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/revoke-sessions`, {
                          method: "POST",
                        });
                      }}
                    >
                      {t("access.revokeSessions")}
                    </ConfirmDialog>
                  </div>
                ) : (
                  <span className="text-muted">{t("access.readOnly")}</span>
                )}
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={4} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={4}>{t("access.empty")}</TableEmpty> : null}
        </TableBody>
      </Table>
      {me?.is_super_admin ? (
        <Card className="mt-4">
          <CardHeader>
            <div>
              <CardTitle>{t("access.gdprHeading")}</CardTitle>
              <CardDescription>{t("access.gdprHelp")}</CardDescription>
            </div>
          </CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-full max-w-xs"
              value={forgetSubject}
              onChange={(e) => setForgetSubject(e.target.value)}
              placeholder={t("access.forgetPlaceholder")}
              aria-label={t("access.forgetPlaceholder")}
            />
            {/* Erasure is irreversible: the operator types the subject being erased, as granting a
                role asks for the role, so the confirmation names what it confirms (audit F51). */}
            <ConfirmDialog
              danger
              size="md"
              title={t("access.forgetTitle", { subject: forgetSubject.trim() })}
              confirmText={forgetSubject.trim()}
              disabled={!forgetSubject.trim()}
              onConfirm={async () => {
                setForgetResult(null);
                const body = await api<ForgetResult>(
                  `/admin/users/${encodeURIComponent(forgetSubject.trim())}/forget`,
                  { method: "POST" },
                );
                setForgetResult(body);
              }}
            >
              {t("access.forget")}
            </ConfirmDialog>
          </div>
          {forgetResult ? <ForgetOutcome result={forgetResult} /> : null}
        </Card>
      ) : null}
    </>
  );
}

// The gateway answers 200 for a partial erasure too: `forgotten` is false, `failed` names the
// stores it could not clear, and the users row is kept (api/admin_platform.py erase_account).
// Showing only the counts made a partial erasure read as done (audit INT-008).
function ForgetOutcome({ result }: { result: ForgetResult }) {
  const { t } = useI18n();
  const failed = result.failed ?? [];
  const complete = result.forgotten === true && failed.length === 0;
  return (
    <div className="mt-3 space-y-2 motion-safe:animate-fade-up">
      {complete ? (
        <p data-testid="forget-status" className="text-[13px] text-ok">
          {t("access.erasureComplete")}
        </p>
      ) : (
        <Alert data-testid="forget-status" role="alert" className="mb-0">
          {failed.length
            ? t("access.erasureFailedFor", { stores: failed.join(", ") })
            : t("access.erasureUnconfirmed")}
        </Alert>
      )}
      <p
        data-testid="forget-counts"
        className="rounded-sm bg-surface-variant px-3 py-2 font-mono text-xs break-all"
      >
        {JSON.stringify(result.counts ?? {})}
      </p>
      {result.not_erased?.length ? (
        <div data-testid="forget-not-erased" className="text-[13px]">
          <p className="text-muted">{t("access.notErased")}</p>
          <ul className="list-disc ps-5">
            {result.not_erased.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
