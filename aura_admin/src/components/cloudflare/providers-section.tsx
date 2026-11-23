"use client";

import Link from "next/link";
import { Pencil, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { CloudflareOverview, CloudflareProviderRow } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

import { RemoveProvider } from "./remove-provider";

const LIMITS = ["rpm", "rpd", "tpm", "tpd"] as const;

/** The Cloudflare providers the gateway serves from, with Add, Replace and Remove. */
export function ProvidersSection({
  overview,
  onAdd,
  onReplace,
  onRemoved,
}: {
  overview: CloudflareOverview;
  onAdd: () => void;
  onReplace: (row: CloudflareProviderRow) => void;
  onRemoved: (name: string) => void;
}) {
  const { t } = useI18n();
  const rows = overview.providers;
  const anyPassive = rows.some((row) => row.health_probe === "passive");

  return (
    <section aria-labelledby="cf-providers" className="flex flex-col gap-2" data-testid="cf-providers">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="cf-providers" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">
          {t("settings.cloudflare.providers.title")}
        </h3>
        {overview.gateway_ready ? (
          <Button size="sm" onClick={onAdd}>
            <Plus aria-hidden="true" />
            {t("settings.cloudflare.providers.add")}
          </Button>
        ) : null}
      </div>
      <p className="text-[12.5px] text-muted">{t("settings.cloudflare.providers.intro")}</p>
      {!overview.gateway_ready ? (
        <p className="text-[13px] text-muted" data-testid="cf-providers-need-railway">
          {t("settings.cloudflare.providers.needRailway")}{" "}
          <Link className="underline" href="/settings">{t("settings.tab.overview")}</Link>
        </p>
      ) : (
        <Card className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("settings.cloudflare.providers.col.name")}</TableHead>
                <TableHead>{t("settings.cloudflare.providers.col.model")}</TableHead>
                <TableHead>{t("settings.cloudflare.providers.col.health")}</TableHead>
                <TableHead>{t("settings.cloudflare.providers.col.key")}</TableHead>
                <TableHead>{t("settings.cloudflare.providers.col.enabled")}</TableHead>
                <TableHead>{t("settings.cloudflare.providers.col.limits")}</TableHead>
                <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableEmpty colSpan={7}>{t("settings.cloudflare.providers.empty")}</TableEmpty>
              ) : null}
              {rows.map((row) => (
                <TableRow key={row.name} data-testid={`cf-provider-${row.name}`}>
                  <TableCell><code dir="ltr" className="text-[12.5px] font-semibold">{row.name}</code></TableCell>
                  <TableCell><code dir="ltr" className="text-[12px] break-all">{row.model}</code></TableCell>
                  <TableCell>
                    <div className="flex flex-col items-start gap-1">
                      {row.healthy === null ? (
                        <Badge>{t("settings.cloudflare.providers.healthUnknown")}</Badge>
                      ) : (
                        <Badge tone={row.healthy ? "ok" : "bad"} dot>
                          {row.healthy ? t("settings.cloudflare.providers.healthy") : t("settings.cloudflare.providers.unhealthy")}
                        </Badge>
                      )}
                      {row.health_probe === "passive" ? (
                        <span className="text-[11.5px] text-faint" title={t("settings.cloudflare.providers.passive")}>
                          {t("settings.cloudflare.providers.passiveShort")}
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge tone={row.key_set ? "ok" : "warn"}>
                      {row.key_set ? t("settings.cloudflare.providers.keySet") : t("settings.cloudflare.providers.keyNotSet")}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge tone={row.enabled ? "ok" : "neutral"}>
                      {row.enabled ? t("settings.cloudflare.providers.on") : t("settings.cloudflare.providers.off")}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-[12.5px] text-muted">
                    {LIMITS.every((key) => !row[key]) ? (
                      t("settings.cloudflare.providers.noLimits")
                    ) : (
                      <ul className="flex flex-col">
                        {LIMITS.filter((key) => row[key] > 0).map((key) => (
                          <li key={key} data-testid={`cf-limit-${key}`}>
                            <bdi dir="ltr">{row[key].toLocaleString("en")}</bdi>{" "}
                            {t(`settings.cloudflare.providers.unit.${key}` as MessageKey)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </TableCell>
                  <TableCell className="text-end">
                    <div className="flex flex-wrap justify-end gap-1.5">
                      <Button
                        size="sm"
                        aria-label={t("settings.cloudflare.providers.replaceFor", { name: row.name })}
                        onClick={() => onReplace(row)}
                      >
                        <Pencil aria-hidden="true" />
                        {t("settings.cloudflare.providers.replace")}
                      </Button>
                      <RemoveProvider row={row} onDone={onRemoved} />
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      {overview.gateway_ready && anyPassive ? (
        <p className="text-[12px] text-faint" data-testid="cf-passive-note">{t("settings.cloudflare.providers.passive")}</p>
      ) : null}
    </section>
  );
}
