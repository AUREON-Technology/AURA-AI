"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { CloudflareUsage } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

type Loaded = { tick: number; usage?: CloudflareUsage; error?: unknown };

/** Numbers and ids stay left-to-right and in Latin digits in both languages. */
const num = (value: number) => value.toLocaleString("en", { maximumFractionDigits: 1 });

/**
 * Today's Workers AI usage (console plan, stage 6, C3): requests, and neurons against the daily
 * allowance when Cloudflare names a neuron figure. Read-only. Every outcome is a state the page says
 * in words: a token without ``Account Analytics: Read`` and an unreachable Cloudflare are shown as
 * such, never as zero. The allowance is the free-plan figure (the plan tier is not known) and
 * Cloudflare's analytics are not for billing, which the card says.
 */
export function UsagePanel() {
  const { t, errorText } = useI18n();
  const [tick, setTick] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    let live = true;
    api<CloudflareUsage>("/admin/control/cloudflare/usage")
      .then((usage) => live && setLoaded({ tick, usage }))
      .catch((error: unknown) => live && setLoaded({ tick, error }));
    return () => {
      live = false;
    };
  }, [tick]);

  const loading = loaded === null || loaded.tick !== tick;
  const usage = loaded?.usage;

  return (
    <section aria-labelledby="cf-usage-title" className="flex flex-col gap-2" data-testid="cf-usage">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="cf-usage-title" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">
          {t("settings.cloudflare.usage.title")}
        </h3>
        <div className="flex items-center gap-2">
          {usage ? <Badge dir="ltr">{t("settings.cloudflare.usage.day", { day: usage.day })}</Badge> : null}
          <Button
            variant="outline"
            size="sm"
            disabled={loading}
            aria-label={t("settings.cloudflare.usage.refresh")}
            onClick={() => setTick((n) => n + 1)}
          >
            <RefreshCw aria-hidden="true" className={loading ? "motion-safe:animate-spin" : undefined} />
            {t("settings.cloudflare.usage.refresh")}
          </Button>
        </div>
      </div>
      <p className="text-[12.5px] text-muted" data-testid="usage-not-for-billing">{t("settings.cloudflare.usage.note")}</p>

      {loaded?.error ? <Alert className="mb-0">{errorText(loaded.error)}</Alert> : null}
      {loaded === null ? <Skeleton className="h-24 w-full" /> : null}

      {usage?.state === "no_permission" ? (
        <p role="status" data-testid="usage-no-permission" className="rounded-sm border border-warn/40 bg-warn-soft px-3.5 py-2.5 text-[13px]">
          {t("settings.cloudflare.usage.noPermission", { permission: usage.permission })}
        </p>
      ) : null}
      {usage?.state === "unavailable" ? (
        <p role="status" data-testid="usage-unavailable" className="rounded-sm border border-line bg-surface-variant px-3.5 py-2.5 text-[13px]">
          {t("settings.cloudflare.usage.unavailable", { reason: usage.reason || "—" })}
        </p>
      ) : null}
      {usage?.state === "ok" ? <UsageFigures usage={usage} /> : null}
    </section>
  );
}

function UsageFigures({ usage }: { usage: CloudflareUsage }) {
  const { t } = useI18n();
  const total = usage.allowance_neurons;
  const used = usage.neurons;
  const percent = used === null ? null : Math.round((used / total) * 100);
  const tone = percent === null ? "bg-brand" : percent >= 100 ? "bg-bad" : percent >= 80 ? "bg-warn" : "bg-ok";

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-x-10 gap-y-3">
        <div>
          <div className="text-[12.5px] text-muted">{t("settings.cloudflare.usage.requests")}</div>
          <div dir="ltr" className="text-2xl font-semibold" data-testid="usage-requests">
            {usage.requests === null ? "—" : num(usage.requests)}
          </div>
        </div>
        {used !== null ? (
          <div>
            <div className="text-[12.5px] text-muted">{t("settings.cloudflare.usage.neurons")}</div>
            <div dir="ltr" className="text-2xl font-semibold" data-testid="usage-neurons" title={usage.neurons_field || undefined}>
              {num(used)}
            </div>
          </div>
        ) : null}
      </div>

      {used !== null && percent !== null ? (
        <div className="flex flex-col gap-1.5">
          <div
            role="progressbar"
            aria-label={t("settings.cloudflare.usage.neurons")}
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={Math.min(used, total)}
            data-testid="usage-bar"
            className="h-2.5 w-full overflow-hidden rounded-full bg-surface-variant"
            dir="ltr"
          >
            <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.min(percent, 100)}%` }} />
          </div>
          <div className="flex flex-wrap justify-between gap-2 text-[12.5px] text-muted">
            <span data-testid="usage-allowance">
              {t("settings.cloudflare.usage.allowance", { used: num(used), total: num(total) })}
            </span>
            <span data-testid="usage-percent">{t("settings.cloudflare.usage.percent", { percent })}</span>
          </div>
          <p className="text-[12.5px] text-muted">{t("settings.cloudflare.usage.allowanceNote", { total: num(total) })}</p>
        </div>
      ) : (
        <p className="text-[13px] text-muted" data-testid="usage-no-neurons">{t("settings.cloudflare.usage.noNeurons")}</p>
      )}
    </Card>
  );
}
