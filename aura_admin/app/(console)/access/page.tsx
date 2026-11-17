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
import type { ForgetResult, Me, Operator, OperatorRole } from "@/lib/types";

function roleTone(role: string) {
  if (role === "SuperAdmin") return "brand" as const;
  if (role === "Admin") return "ok" as const;
  if (role === "Developer") return "warn" as const;
  return "neutral" as const;
}

export default function AccessPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Operator[]>([]);
  const [forgetSubject, setForgetSubject] = useState("");
  const [grantEmail, setGrantEmail] = useState("");
  const [grantRole, setGrantRole] = useState<OperatorRole>("Developer");
  const [forgetResult, setForgetResult] = useState<ForgetResult | null>(null);
  const [error, setError] = useState<string | null>(null);
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
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }, []);

  return (
    <>
      <PageHeader title="Access">
        Operator directory. Role changes and erasure are SuperAdmin only. A change applies at the
        person&apos;s next sign-in or token refresh (within 15 minutes). A role granted by an env
        list or roles.json can only be lowered there.
      </PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      {me?.is_super_admin ? (
        <Card className="mb-4">
          <CardHeader>
            <div>
              <CardTitle>Grant a role</CardTitle>
              <CardDescription>The person must have signed in once.</CardDescription>
            </div>
          </CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-full max-w-xs"
              value={grantEmail}
              onChange={(e) => setGrantEmail(e.target.value)}
              placeholder="email"
              aria-label="Email"
            />
            <Select
              className="w-auto"
              aria-label="Role"
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
              title={`Set ${grantEmail || "this user"} to ${grantRole}`}
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
              Grant role
            </ConfirmDialog>
          </div>
        </Card>
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Email</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Granted by</TableHead>
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
                        title={`Set ${row.email} to ${role}`}
                        confirmText={role}
                        onConfirm={async () => {
                          await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                            method: "PATCH",
                            body: JSON.stringify({ role }),
                          });
                          await load();
                        }}
                      >
                        {`Make ${role}`}
                      </ConfirmDialog>
                    ))}
                    <ConfirmDialog
                      danger
                      title={`Remove ${row.email}'s Access-page role`}
                      confirmText="User"
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/role`, {
                          method: "PATCH",
                          body: JSON.stringify({ role: "User" }),
                        });
                        await load();
                      }}
                    >
                      Remove access
                    </ConfirmDialog>
                    <ConfirmDialog
                      danger
                      title="Revoke sessions"
                      confirmText="revoke"
                      onConfirm={async () => {
                        await api(`/admin/operators/${encodeURIComponent(row.email)}/revoke-sessions`, {
                          method: "POST",
                        });
                      }}
                    >
                      Revoke sessions
                    </ConfirmDialog>
                  </div>
                ) : (
                  <span className="text-muted">Read only</span>
                )}
              </TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={4} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={4}>No operators yet.</TableEmpty> : null}
        </TableBody>
      </Table>
      {me?.is_super_admin ? (
        <Card className="mt-4">
          <CardHeader>
            <div>
              <CardTitle>GDPR forget</CardTitle>
              <CardDescription>
                Returns counts only. Never displays user content. An incomplete erasure keeps the
                account; run it again once the failed stores are back.
              </CardDescription>
            </div>
          </CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-full max-w-xs"
              value={forgetSubject}
              onChange={(e) => setForgetSubject(e.target.value)}
              placeholder="email or auth subject"
              aria-label="Email or auth subject"
            />
            <ConfirmDialog
              danger
              size="md"
              title="Forget user"
              confirmText="forget"
              onConfirm={async () => {
                setForgetResult(null);
                const body = await api<ForgetResult>(
                  `/admin/users/${encodeURIComponent(forgetSubject)}/forget`,
                  { method: "POST" },
                );
                setForgetResult(body);
              }}
            >
              Forget
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
  const failed = result.failed ?? [];
  const complete = result.forgotten === true && failed.length === 0;
  return (
    <div className="mt-3 space-y-2 motion-safe:animate-fade-up">
      {complete ? (
        <p data-testid="forget-status" className="text-[13px] text-ok">
          Erasure complete.
        </p>
      ) : (
        <Alert data-testid="forget-status" role="alert" className="mb-0">
          {`Erasure incomplete: ${failed.length ? `failed for ${failed.join(", ")}` : "the gateway did not confirm it"}. The account was kept; run forget again.`}
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
          <p className="text-muted">Not erased by this request:</p>
          <ul className="list-disc pl-5">
            {result.not_erased.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
