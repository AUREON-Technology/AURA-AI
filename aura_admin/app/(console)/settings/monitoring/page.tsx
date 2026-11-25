"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CircleCheck, CircleDashed, CircleX, ExternalLink } from "lucide-react";

import { stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AuraApiError, api } from "@/lib/api";
import type { ApplyResult, MonitoringOverview } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

function Value({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[10rem_minmax(0,1fr)]">
      <span className="text-faint">{label}</span>
      <code dir="ltr" className="break-all text-fg">{value}</code>
    </div>
  );
}

export default function MonitoringPage() {
  const { t, errorText } = useI18n();
  const stepUp = useStepUp();
  const [overview, setOverview] = useState<MonitoringOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [applied, setApplied] = useState<ApplyResult | null>(null);

  const load = useCallback(() => {
    api<MonitoringOverview>("/admin/control/monitoring")
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
      <Alert data-testid="monitoring-not-connected">
        {t("settings.monitoring.notConnected")}{" "}
        <Link className="underline" href="/settings">{t("settings.tab.overview")}</Link>
      </Alert>
    ) : (
      <Alert>{errorText(error)}</Alert>
    );
  }
  if (!overview) return <Skeleton className="h-40 w-full" />;

  const scrapeTone = overview.scrape.state === "up" ? "ok" : overview.scrape.state === "down" ? "bad" : "warn";
  return (
    <div className="flex flex-col gap-4" data-testid="monitoring-panel">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
        <span>{t("settings.monitoring.stack", { slug: overview.stack.slug })}</span>
        {overview.stack.grafana_url ? (
          <a className="inline-flex items-center gap-1 underline" href={overview.stack.grafana_url} target="_blank" rel="noreferrer">
            Grafana <ExternalLink aria-hidden="true" className="size-3" />
          </a>
        ) : null}
      </div>
      {overview.errors.length ? (
        <Alert className="mb-0" data-testid="monitoring-errors">
          {t("settings.monitoring.cannotRead")} {overview.errors.join(" · ")}
        </Alert>
      ) : null}

      <Card className="flex flex-col gap-3 p-5" data-testid="monitoring-rules">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[15px] font-semibold">{t("settings.monitoring.rules")}</h2>
          <Badge tone={overview.rules.all_loaded ? "ok" : "warn"}>
            {overview.rules.all_loaded ? t("settings.monitoring.allLoaded") : t("settings.monitoring.notAllLoaded")}
          </Badge>
        </div>
        <p className="text-[13px] text-muted">{t("settings.monitoring.rulesNote", { namespace: overview.rules.namespace })}</p>
        <ul className="grid gap-1 text-[13px] sm:grid-cols-2">
          {overview.rules.groups.map((g) => {
            const result = applied?.results.find((r) => r.group === g.name);
            const ok = result ? result.ok : g.loaded;
            return (
              <li key={g.name} className="flex items-center gap-2" data-testid={`rule-group-${g.name}`}>
                {ok ? <CircleCheck aria-hidden="true" className="size-4 text-ok" /> : <CircleDashed aria-hidden="true" className="size-4 text-faint" />}
                <code dir="ltr">{g.name}</code>
                <span className="text-faint">{t("settings.monitoring.ruleCount", { count: g.rules })}</span>
                {result && !result.ok ? <span className="text-bad">{result.error}</span> : null}
              </li>
            );
          })}
        </ul>
        <div>
          <ConfirmDialog
            title={t("settings.monitoring.applyTitle")}
            confirmText="grafana"
            onConfirm={async () => {
              try {
                const result = await stepUp.write<ApplyResult>("/admin/control/monitoring/apply", { confirm: "grafana" });
                setApplied(result);
                load();
              } catch (err) {
                throw stepUpError(err, t);
              }
            }}
            body={<p className="text-[13px] text-muted">{t("settings.monitoring.applyBody")}</p>}
          >
            {t("settings.monitoring.apply")}
          </ConfirmDialog>
        </div>
        {applied ? (
          <p className={applied.status === "failed" ? "text-[13px] text-bad" : "text-[13px] text-ok"} role="status" data-testid="monitoring-applied">
            {applied.status === "failed" ? t("settings.monitoring.appliedPartly", { reason: applied.reason }) : t("settings.monitoring.applied")}
          </p>
        ) : null}
      </Card>

      <Card className="flex flex-col gap-3 p-5" data-testid="monitoring-scrape">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[15px] font-semibold">{t("settings.monitoring.scrape")}</h2>
          <Badge tone={scrapeTone} dot data-testid="scrape-state">{t(`settings.monitoring.scrape.${overview.scrape.state}` as MessageKey)}</Badge>
        </div>
        <p className="text-[13px] text-muted">{t("settings.monitoring.scrapeNote")}</p>
        <div className="flex flex-col gap-1.5 text-[13px]">
          <Value label={t("settings.monitoring.jobName")} value={overview.scrape.job} />
          <Value label="URL" value={overview.scrape.url} />
          <Value label={t("settings.monitoring.interval")} value={`${overview.scrape.interval_s} s`} />
          <Value label={t("settings.monitoring.auth")} value={overview.scrape.auth} />
        </div>
        {!overview.scrape.metrics_token_set ? (
          <Alert className="mb-0" data-testid="metrics-token-missing">{t("settings.monitoring.metricsTokenMissing")}</Alert>
        ) : null}
      </Card>

      <Card className="flex flex-col gap-3 p-5" data-testid="monitoring-uptime">
        <h2 className="text-[15px] font-semibold">{t("settings.monitoring.uptime")}</h2>
        <p className="text-[13px] text-muted">{t("settings.monitoring.uptimeNote")}</p>
        {overview.uptime.checks.map((check) => {
          const seenRow = overview.uptime.observed.find((o) => o.instance === check.url || o.job === check.name);
          const ratio = seenRow?.success_ratio;
          return (
            <div key={check.name} className="flex flex-col gap-1.5 border-t border-line pt-3 text-[13px] first:border-t-0 first:pt-0" data-testid="uptime-check">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{check.name}</span>
                {ratio === undefined || ratio === null ? (
                  <Badge>{t("settings.monitoring.noData")}</Badge>
                ) : (
                  <Badge tone={ratio >= 0.99 ? "ok" : "bad"}>{Math.round(ratio * 1000) / 10}%</Badge>
                )}
              </div>
              <Value label="URL" value={check.url} />
              <Value label={t("settings.monitoring.frequency")} value={`${check.frequency_s} s`} />
              <Value label={t("settings.monitoring.success")} value={check.success} />
            </div>
          );
        })}
      </Card>

      <Card className="flex flex-col gap-2 p-5" data-testid="monitoring-firing">
        <h2 className="text-[15px] font-semibold">{t("settings.monitoring.firing")}</h2>
        {overview.firing.length === 0 ? (
          <p className="text-[13px] text-muted">{overview.errors.length ? t("settings.monitoring.firingUnknown") : t("settings.monitoring.noneFiring")}</p>
        ) : (
          <ul className="flex flex-col gap-1 text-[13px]">
            {overview.firing.map((a, i) => (
              <li key={`${a.alertname}-${i}`} className="flex items-center gap-2">
                <CircleX aria-hidden="true" className="size-4 text-bad" />
                <code dir="ltr">{a.alertname}</code>
                {a.severity ? <Badge tone={a.severity === "critical" ? "bad" : "warn"}>{a.severity}</Badge> : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
