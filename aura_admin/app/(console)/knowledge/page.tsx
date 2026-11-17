"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { api, fetchMe } from "@/lib/api";
import type { KnowledgeSource, Me } from "@/lib/types";

export default function KnowledgePage() {
  const [me, setMe] = useState<Me | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [indexes, setIndexes] = useState<{ physical_name: string; active: boolean }[]>([]);
  const [id, setId] = useState("");
  const [uri, setUri] = useState("https://");
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

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
    load()
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoaded(true));
  }, []);

  return (
    <>
      <PageHeader title="Knowledge">Approved sources and versioned indexes. Not a user document browser.</PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Sources</CardTitle>
            <CardDescription>Only enabled sources are crawled into the next index.</CardDescription>
          </div>
        </CardHeader>
        <Table flush>
          <TableHeader>
            <TableRow>
              <TableHead>Id</TableHead>
              <TableHead>URI</TableHead>
              <TableHead>Enabled</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sources.map((source) => (
              <TableRow key={source.id}>
                <TableCell className="font-medium">{source.id}</TableCell>
                <TableCell className="max-w-[360px] truncate text-muted">{source.uri}</TableCell>
                <TableCell>
                  <Badge tone={source.enabled ? "ok" : "neutral"} dot>
                    {source.enabled ? "yes" : "no"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap justify-end gap-1.5">
                    <Button
                      size="sm"
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
                            `${source.enabled ? "Disable" : "Enable"} ${source.id} failed: ${
                              err instanceof Error ? err.message : "request failed"
                            }`,
                          );
                        }
                      }}
                    >
                      {source.enabled ? "Disable" : "Enable"}
                    </Button>
                    {me?.is_super_admin ? (
                      <ConfirmDialog
                        danger
                        title={`Delete ${source.id}`}
                        confirmText={source.id}
                        onConfirm={async () => {
                          await api(`/admin/sources/${encodeURIComponent(source.id)}`, { method: "DELETE" });
                          await load();
                        }}
                      >
                        Delete
                      </ConfirmDialog>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!loaded ? <TableSkeleton cols={4} /> : null}
            {loaded && sources.length === 0 && !error ? <TableEmpty colSpan={4}>No sources registered.</TableEmpty> : null}
          </TableBody>
        </Table>
        {me?.is_super_admin ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <Field>
              Id
              {/* Same rule as the gateway's SourceCreateRequest: the id is a URL path segment. */}
              <Input
                value={id}
                onChange={(e) => setId(e.target.value)}
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
                maxLength={128}
                title="Letters, digits, '.', '_', ':' or '-', starting with a letter or digit"
              />
            </Field>
            <Field className="min-w-[240px] flex-1">
              HTTPS URI
              <Input value={uri} onChange={(e) => setUri(e.target.value)} />
            </Field>
            <ConfirmDialog
              size="md"
              title="Register source"
              confirmText={id || "source"}
              onConfirm={async () => {
                await api("/admin/sources", {
                  method: "POST",
                  body: JSON.stringify({ id, type: "web", uri }),
                });
                setId("");
                await load();
              }}
            >
              Register
            </ConfirmDialog>
          </div>
        ) : null}
      </Card>
      <Card className="mt-4">
        <CardHeader>
          <div>
            <CardTitle>Indexes</CardTitle>
            <CardDescription>The active alias serves retrieval; roll back to any earlier build.</CardDescription>
          </div>
          <ConfirmDialog
            title="Rebuild index"
            confirmText="rebuild"
            onConfirm={async () => {
              await api("/admin/index/rebuild", { method: "POST" });
              await load();
            }}
          >
            Rebuild
          </ConfirmDialog>
        </CardHeader>
        <Table flush>
          <TableHeader>
            <TableRow>
              <TableHead>Physical name</TableHead>
              <TableHead>Active</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {indexes.map((row) => (
              <TableRow key={row.physical_name}>
                <TableCell>
                  <code className="text-xs">{row.physical_name}</code>
                </TableCell>
                <TableCell>
                  {row.active ? (
                    <Badge tone="ok" dot pulse>
                      yes
                    </Badge>
                  ) : (
                    ""
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {row.active ? null : (
                    <ConfirmDialog
                      title="Rollback alias"
                      confirmText="rollback"
                      onConfirm={async () => {
                        await api("/admin/index/rollback", {
                          method: "POST",
                          body: JSON.stringify({ physical_name: row.physical_name }),
                        });
                        await load();
                      }}
                    >
                      Rollback
                    </ConfirmDialog>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!loaded ? <TableSkeleton cols={3} rows={2} /> : null}
            {loaded && indexes.length === 0 && !error ? <TableEmpty colSpan={3}>No index builds yet.</TableEmpty> : null}
          </TableBody>
        </Table>
      </Card>
    </>
  );
}
