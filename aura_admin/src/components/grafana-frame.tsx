"use client";

import { useI18n } from "@/lib/i18n/context";

export function GrafanaFrame({ title, uid }: { title: string; uid: string }) {
  const { t } = useI18n();
  const embed = process.env.NEXT_PUBLIC_GRAFANA_EMBED === "1";
  const src = `/grafana/d/${uid}?orgId=1&kiosk`;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>{title}</h2>
        <a className="btn" href={src} target="_blank" rel="noreferrer">
          {t("grafana.open")}
        </a>
      </div>
      {embed ? <iframe className="grafana" title={title} src={src} /> : null}
    </div>
  );
}
