"use client";

import { useEffect, useMemo, useState } from "react";
import { Lock, Search } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { CatalogEntry, CatalogGroup } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function Row({ entry }: { entry: CatalogEntry }) {
  const { t } = useI18n();
  return (
    <div className="grid gap-1.5 border-b border-line px-5 py-3 last:border-b-0 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="min-w-0">
        <code dir="ltr" className="block break-all text-[12.5px] font-semibold text-fg">{entry.variable}</code>
        {entry.description ? <p className="mt-1 text-[12.5px] text-muted">{entry.description}</p> : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-[12.5px]">
        {entry.secret ? (
          <Badge tone={entry.set ? "ok" : "neutral"} data-testid="secret-badge">
            <Lock aria-hidden="true" className="size-3" />
            {t("settings.aura.secret")} · {entry.set ? t("settings.aura.set") : t("settings.aura.notSet")}
          </Badge>
        ) : (
          <code dir="ltr" className="max-w-full break-all rounded bg-surface-variant px-1.5 py-0.5 text-fg">{show(entry.value)}</code>
        )}
        {!entry.secret && !entry.set ? <Badge>{t("settings.aura.notSet")}</Badge> : null}
        {entry.protected ? <Badge tone="warn">{t("settings.aura.protected")}</Badge> : null}
        {!entry.secret && entry.default !== null && entry.default !== undefined ? (
          <span className="text-faint">{t("settings.aura.default", { value: show(entry.default) })}</span>
        ) : null}
        {entry.minimum !== null && entry.maximum !== null ? (
          <span className="text-faint">{t("settings.aura.range", { min: entry.minimum, max: entry.maximum })}</span>
        ) : null}
      </div>
    </div>
  );
}

export default function AuraSettingsPage() {
  const { t, errorText } = useI18n();
  const [groups, setGroups] = useState<CatalogGroup[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api<{ groups: CatalogGroup[] }>("/admin/control/settings")
      .then((body) => setGroups(body.groups))
      .catch((err: unknown) => setError(err));
  }, []);

  const filtered = useMemo(() => {
    if (!groups) return [];
    const needle = query.trim().toLowerCase();
    return groups
      .map((group) => ({
        ...group,
        settings: needle
          ? group.settings.filter((e) =>
              [e.variable, e.field, e.description, e.section].some((text) => text.toLowerCase().includes(needle)),
            )
          : group.settings,
      }))
      .filter((group) => group.settings.length > 0);
  }, [groups, query]);

  if (error) return <Alert>{errorText(error)}</Alert>;
  if (!groups) return <Skeleton className="h-60 w-full" />;
  const total = filtered.reduce((n, g) => n + g.settings.length, 0);
  return (
    <div className="flex flex-col gap-4">
      <Alert className="mb-0 border-line bg-surface-variant text-muted">{t("settings.aura.readOnlyNote")}</Alert>
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">{t("settings.aura.search")}</span>
          <Search aria-hidden="true" className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input
            className="ps-9"
            placeholder={t("settings.aura.search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <span className="text-[13px] text-muted" data-testid="settings-count">{t("settings.aura.count", { count: total })}</span>
      </div>
      {filtered.length === 0 ? <p className="text-muted">{t("settings.aura.none")}</p> : null}
      {filtered.map((group) => (
        <section key={group.section} aria-labelledby={`group-${group.section}`}>
          <h2 id={`group-${group.section}`} className="mb-2 text-[13px] font-semibold tracking-[0.06em] text-faint uppercase" dir="ltr">
            {group.section}
          </h2>
          <Card className="p-0">
            {group.settings.map((entry) => (
              <Row key={`${entry.section}.${entry.variable}`} entry={entry} />
            ))}
          </Card>
        </section>
      ))}
    </div>
  );
}
