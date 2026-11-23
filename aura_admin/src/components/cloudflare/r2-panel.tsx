"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { CloudflareBucketRow, CloudflareR2 } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

type Loaded = { tick: number; r2?: CloudflareR2; error?: unknown };

/**
 * The account's R2 buckets (console plan, stage 6, C3), read-only: name, creation time, location and
 * the number of bucket lock rules where Cloudflare says. "R2 is not enabled on this account" is a
 * first-class state (Cloudflare answers error 10042 until R2 is turned on in its dashboard), as are a
 * missing permission and an unreachable Cloudflare. Nothing here creates, locks or deletes a bucket.
 */
export function R2Panel() {
  const { t, errorText } = useI18n();
  const [tick, setTick] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    let live = true;
    api<CloudflareR2>("/admin/control/cloudflare/r2")
      .then((r2) => live && setLoaded({ tick, r2 }))
      .catch((error: unknown) => live && setLoaded({ tick, error }));
    return () => {
      live = false;
    };
  }, [tick]);

  const loading = loaded === null || loaded.tick !== tick;
  const r2 = loaded?.r2;

  return (
    <section aria-labelledby="cf-r2-title" className="flex flex-col gap-2" data-testid="cf-r2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="cf-r2-title" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">
          {t("settings.cloudflare.r2.title")}
        </h3>
        <Button
          variant="outline"
          size="sm"
          disabled={loading}
          aria-label={t("settings.cloudflare.r2.refresh")}
          onClick={() => setTick((n) => n + 1)}
        >
          <RefreshCw aria-hidden="true" className={loading ? "motion-safe:animate-spin" : undefined} />
          {t("settings.cloudflare.r2.refresh")}
        </Button>
      </div>
      <p className="text-[12.5px] text-muted">{t("settings.cloudflare.r2.note")}</p>

      {loaded?.error ? <Alert className="mb-0">{errorText(loaded.error)}</Alert> : null}
      {loaded === null ? <Skeleton className="h-24 w-full" /> : null}

      {r2?.state === "not_enabled" ? (
        <p role="status" data-testid="r2-not-enabled" className="rounded-sm border border-line bg-surface-variant px-3.5 py-2.5 text-[13px]">
          {t("settings.cloudflare.r2.notEnabled")}
        </p>
      ) : null}
      {r2?.state === "no_permission" ? (
        <p role="status" data-testid="r2-no-permission" className="rounded-sm border border-warn/40 bg-warn-soft px-3.5 py-2.5 text-[13px]">
          {t("settings.cloudflare.r2.noPermission", { permission: r2.permission })}
        </p>
      ) : null}
      {r2?.state === "unavailable" ? (
        <p role="status" data-testid="r2-unavailable" className="rounded-sm border border-line bg-surface-variant px-3.5 py-2.5 text-[13px]">
          {t("settings.cloudflare.r2.unavailable", { reason: r2.reason || "—" })}
        </p>
      ) : null}
      {r2?.state === "ok" ? <Buckets r2={r2} /> : null}
    </section>
  );
}

function Buckets({ r2 }: { r2: CloudflareR2 }) {
  const { t, lang } = useI18n();

  function lock(bucket: CloudflareBucketRow) {
    if (bucket.lock_rules === null) return <Badge>{t("settings.cloudflare.r2.lockUnknown")}</Badge>;
    if (bucket.lock_rules === 0) return <Badge>{t("settings.cloudflare.r2.lockNone")}</Badge>;
    return <Badge tone="ok">{t("settings.cloudflare.r2.lockRules", { count: bucket.lock_rules })}</Badge>;
  }

  return (
    <>
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.cloudflare.r2.name")}</TableHead>
              <TableHead>{t("settings.cloudflare.r2.created")}</TableHead>
              <TableHead>{t("settings.cloudflare.r2.location")}</TableHead>
              <TableHead>{t("settings.cloudflare.r2.lock")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {r2.buckets.length === 0 ? (
              <TableEmpty colSpan={4}><span data-testid="r2-empty">{t("settings.cloudflare.r2.empty")}</span></TableEmpty>
            ) : null}
            {r2.buckets.map((bucket) => (
              <TableRow key={bucket.name} data-testid={`bucket-${bucket.name}`}>
                <TableCell><code dir="ltr" className="text-[12.5px]">{bucket.name}</code></TableCell>
                <TableCell className="whitespace-nowrap text-muted">
                  {bucket.created && !Number.isNaN(Date.parse(bucket.created))
                    ? new Date(bucket.created).toLocaleString(lang === "ar" ? "ar" : "en")
                    : "—"}
                </TableCell>
                <TableCell dir="ltr" className="text-muted">{bucket.location || "—"}</TableCell>
                <TableCell>{lock(bucket)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {r2.more ? <p className="text-[12.5px] text-muted" data-testid="r2-more">{t("settings.cloudflare.r2.more")}</p> : null}
    </>
  );
}
