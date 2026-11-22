"use client";

import { ExternalLink } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n/context";

export function GrafanaFrame({ title, uid }: { title: string; uid: string }) {
  const { t } = useI18n();
  const embed = process.env.NEXT_PUBLIC_GRAFANA_EMBED === "1";
  const base = (process.env.NEXT_PUBLIC_GRAFANA_BASE_URL || "/grafana").replace(/\/+$/, "");
  const src = `${base}/d/${uid}?orgId=1&kiosk`;
  return (
    <Card className="mt-4">
      <CardHeader className={embed ? undefined : "mb-0 items-center"}>
        <div className="min-w-0">
          <CardTitle>{title}</CardTitle>
          <CardDescription>
            {t("grafana.dashboard")} <code className="text-xs">{uid}</code>
          </CardDescription>
        </div>
        <a className={buttonVariants({ size: "sm" })} href={src} target="_blank" rel="noreferrer">
          {t("grafana.open")}
          <ExternalLink aria-hidden="true" className="rtl:-scale-x-100" />
        </a>
      </CardHeader>
      {embed ? (
        <iframe
          className="h-[420px] w-full rounded-sm border border-line bg-black"
          title={title}
          src={src}
        />
      ) : null}
    </Card>
  );
}
