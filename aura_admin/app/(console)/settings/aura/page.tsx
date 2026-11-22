"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Lock, Pencil, RotateCcw, Search } from "lucide-react";

import { ChangeTray } from "@/components/change-tray";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { CatalogEntry, CatalogGroup, PendingChanges } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

type SettingsBody = {
  source: "process" | "railway";
  editable: boolean;
  service_id?: string;
  service_name?: string;
  groups: CatalogGroup[];
};

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** What the editor starts from: the platform's raw value, else the effective one. */
function initial(entry: CatalogEntry): string {
  if (entry.raw !== null && entry.raw !== undefined) return entry.raw;
  if (entry.value === null || entry.value === undefined) return "";
  if (Array.isArray(entry.value)) return entry.value.join(",");
  return typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value);
}

function Editor({ entry, onStage, onCancel }: { entry: CatalogEntry; onStage: (value: string) => void; onCancel: () => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(entry.secret ? "" : initial(entry));
  const label = t("settings.railway.newValue", { name: entry.variable });
  return (
    <form
      className="flex w-full flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onStage(draft);
      }}
    >
      {entry.type === "bool" ? (
        <Select aria-label={label} value={draft} onChange={(e) => setDraft(e.target.value)} className="w-32">
          <option value="true">true</option>
          <option value="false">false</option>
        </Select>
      ) : (
        <Input
          dir="ltr"
          aria-label={label}
          type={entry.secret ? "password" : entry.type === "int" || entry.type === "float" ? "number" : "text"}
          min={entry.minimum ?? undefined}
          max={entry.maximum ?? undefined}
          step={entry.type === "float" ? "any" : undefined}
          autoComplete="off"
          spellCheck={false}
          className="min-w-40 flex-1"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      )}
      <Button type="submit" size="sm">{t("settings.railway.stage")}</Button>
      <Button variant="ghost" size="sm" onClick={onCancel}>{t("common.cancel")}</Button>
    </form>
  );
}

function Row({
  entry,
  editable,
  pending,
  onStage,
}: {
  entry: CatalogEntry;
  editable: boolean;
  pending: boolean;
  onStage: (variable: string, value: string | null) => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const canEdit = editable && !entry.protected;
  return (
    <div className="grid gap-1.5 border-b border-line px-5 py-3 last:border-b-0 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" data-testid={`setting-${entry.variable}`}>
      <div className="min-w-0">
        <code dir="ltr" className="block break-all text-[12.5px] font-semibold text-fg">{entry.variable}</code>
        {entry.description ? <p className="mt-1 text-[12.5px] text-muted">{entry.description}</p> : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-[12.5px]">
        {editing ? (
          <Editor
            entry={entry}
            onCancel={() => setEditing(false)}
            onStage={(value) => {
              onStage(entry.variable, value);
              setEditing(false);
            }}
          />
        ) : (
          <>
            {entry.secret ? (
              <Badge tone={entry.set ? "ok" : "neutral"} data-testid="secret-badge">
                <Lock aria-hidden="true" className="size-3" />
                {t("settings.aura.secret")} · {entry.set ? t("settings.aura.set") : t("settings.aura.notSet")}
              </Badge>
            ) : (
              <code dir="ltr" className="max-w-full break-all rounded bg-surface-variant px-1.5 py-0.5 text-fg">{show(entry.value)}</code>
            )}
            {!entry.secret && !entry.set ? <Badge>{t("settings.aura.notSet")}</Badge> : null}
            {!entry.secret && entry.raw !== null && entry.raw !== undefined && entry.raw !== initialEffective(entry) ? (
              <Badge tone="warn" data-testid="next-deploy">{t("settings.aura.nextDeploy", { value: entry.raw })}</Badge>
            ) : null}
            {entry.protected ? <Badge tone="warn">{t("settings.aura.protected")}</Badge> : null}
            {pending ? <Badge tone="warn">{t("settings.railway.pending")}</Badge> : null}
            {!entry.secret && entry.default !== null && entry.default !== undefined ? (
              <span className="text-faint">{t("settings.aura.default", { value: show(entry.default) })}</span>
            ) : null}
            {entry.minimum !== null && entry.maximum !== null ? (
              <span className="text-faint">{t("settings.aura.range", { min: entry.minimum, max: entry.maximum })}</span>
            ) : null}
            {canEdit ? (
              <span className="ms-auto flex gap-1">
                <Button variant="ghost" size="icon" aria-label={t("settings.railway.edit", { name: entry.variable })} onClick={() => setEditing(true)}>
                  <Pencil aria-hidden="true" />
                </Button>
                {entry.set ? (
                  <Button variant="ghost" size="icon" aria-label={t("settings.aura.reset", { name: entry.variable })} onClick={() => onStage(entry.variable, null)}>
                    <RotateCcw aria-hidden="true" />
                  </Button>
                ) : null}
              </span>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

/** The effective value written the way the platform would hold it, to spot an undeployed change. */
function initialEffective(entry: CatalogEntry): string {
  const value = entry.value;
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(",");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export default function AuraSettingsPage() {
  const { t, errorText } = useI18n();
  const [body, setBody] = useState<SettingsBody | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<PendingChanges>({});
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<SettingsBody>("/admin/control/settings")
      .then((b) => {
        setBody(b);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  const groups = body?.groups ?? null;
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

  const secretNames = useMemo(
    () => new Set((groups ?? []).flatMap((g) => g.settings).filter((e) => e.secret).map((e) => e.variable)),
    [groups],
  );

  if (error && !body) return <Alert>{errorText(error)}</Alert>;
  if (!body || !groups) return <Skeleton className="h-60 w-full" />;
  const total = filtered.reduce((n, g) => n + g.settings.length, 0);
  const editable = body.editable && Boolean(body.service_id && body.service_name);
  return (
    <div className="flex flex-col gap-4">
      <Alert className="mb-0 border-line bg-surface-variant text-muted" data-testid="settings-source">
        {editable ? t("settings.aura.editableNote", { service: body.service_name ?? "" }) : t("settings.aura.readOnlyNote")}
      </Alert>
      {notice ? <p className="text-[13px] text-ok" role="status" data-testid="settings-notice">{notice}</p> : null}
      {editable && body.service_id && body.service_name ? (
        <ChangeTray
          serviceId={body.service_id}
          serviceName={body.service_name}
          changes={pending}
          secretNames={secretNames}
          onRemove={(name) => setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== name)))}
          onClear={() => setPending({})}
          onApplied={() => {
            setPending({});
            setNotice(t("settings.railway.applied"));
            load();
          }}
        />
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">{t("settings.aura.search")}</span>
          <Search aria-hidden="true" className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input className="ps-9" placeholder={t("settings.aura.search")} value={query} onChange={(e) => setQuery(e.target.value)} />
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
              <Row
                key={`${entry.section}.${entry.variable}`}
                entry={entry}
                editable={editable}
                pending={entry.variable in pending}
                onStage={(variable, value) => setPending((p) => ({ ...p, [variable]: value }))}
              />
            ))}
          </Card>
        </section>
      ))}
    </div>
  );
}
