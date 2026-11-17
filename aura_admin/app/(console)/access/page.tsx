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

function roleTone(role: string) {
  if (role === "SuperAdmin") return "brand" as const;
  if (role === "Admin") return "ok" as const;
  if (role === "Developer") return "warn" as const;
  return "neutral" as const;
}
import type { Me, Operator, OperatorRole } from "@/lib/types";

export default function AccessPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Operator[]>([]);
  const [forgetSubject, setForgetSubject] = useState("");
  const [grantEmail, setGrantEmail] = useState("");
  const [grantRole, setGrantRole] = useState<OperatorRole>("Developer");
  const [counts, setCounts] = useState<string>("");
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
              <CardDescription>Returns counts only. Never displays user content.</CardDescription>
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
                const body = await api<{ counts: Record<string, number> }>(
                  `/admin/users/${encodeURIComponent(forgetSubject)}/forget`,
                  { method: "POST" },
                );
                setCounts(JSON.stringify(body.counts));
              }}
            >
              Forget
            </ConfirmDialog>
          </div>
          {counts ? (
            <p
              data-testid="forget-counts"
              className="mt-3 rounded-sm bg-surface-variant px-3 py-2 font-mono text-xs break-all motion-safe:animate-fade-up"
            >
              {counts}
            </p>
          ) : null}
        </Card>
      ) : null}
    </>
  );
}
