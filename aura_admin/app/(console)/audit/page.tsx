"use client";

import { useCallback, useEffect, useState } from "react";

import { ListFilter } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";

export default function AuditPage() {
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [action, setAction] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  // Stable across renders (state setters are), so the mount-time effect below can list it as its
  // one dependency instead of silencing react-hooks/exhaustive-deps (audit G-10 lint gate).
  const load = useCallback((next: string) => {
    const query = next ? `?action=${encodeURIComponent(next)}` : "";
    api<{ events: AuditEvent[] }>(`/admin/audit${query}`)
      .then((body) => setRows(body.events))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  return (
    <>
      <PageHeader title="Audit">Append-only. Arguments are digests, never verbatim user content.</PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      <form
        className="mb-4 flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          load(action);
        }}
      >
        <Input
          className="w-full max-w-xs"
          value={action}
          onChange={(e) => setAction(e.target.value)}
          placeholder="action prefix"
          aria-label="Action prefix"
        />
        <Button type="submit">
          <ListFilter aria-hidden="true" />
          Filter
        </Button>
      </form>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>When</TableHead>
            <TableHead>Actor</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Resource</TableHead>
            <TableHead>Outcome</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={`${row.at}-${index}`}>
              <TableCell className="whitespace-nowrap text-muted">{row.at}</TableCell>
              <TableCell>
                {row.actor} <span className="text-muted">({row.actor_role})</span>
              </TableCell>
              <TableCell>
                <code className="text-xs">{row.action}</code>
              </TableCell>
              <TableCell>{row.resource}</TableCell>
              <TableCell>{row.outcome}</TableCell>
            </TableRow>
          ))}
          {!loaded ? <TableSkeleton cols={5} /> : null}
          {loaded && rows.length === 0 && !error ? <TableEmpty colSpan={5}>No audit events match.</TableEmpty> : null}
        </TableBody>
      </Table>
    </>
  );
}
