"use client";

import { useEffect, useState } from "react";
import { Plus } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { CloudflareCatalog, CloudflareCatalogRow, CloudflareOverview } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

import { refusalKey } from "./refusal";

/** Cloudflare's own task names: they are what the catalog filters on, so they are not translated. */
const TASKS = [
  "Text Generation",
  "Text Embeddings",
  "Text-to-Image",
  "Image-to-Text",
  "Image Classification",
  "Object Detection",
  "Automatic Speech Recognition",
  "Text-to-Speech",
  "Translation",
  "Summarization",
  "Text Classification",
];
const PER_PAGE = 20;

type Plan = "any" | "free" | "paid";

function price(entry: CloudflareCatalogRow["price"][number]): string {
  return [entry.price, entry.currency, entry.unit].filter((part) => part !== undefined && part !== "").join(" ");
}

/** Cloudflare's live Workers AI catalog, with what AURA will and will not admit, and an Add button on the usable ones. */
export function ModelsSection({
  overview,
  onAdd,
}: {
  overview: CloudflareOverview;
  onAdd: (row: CloudflareCatalogRow) => void;
}) {
  const { t, errorText } = useI18n();
  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState("");
  const [task, setTask] = useState("Text Generation");
  const [plan, setPlan] = useState<Plan>("any");
  const [vision, setVision] = useState(false);
  const [usable, setUsable] = useState(false);
  const [page, setPage] = useState(1);
  const [catalog, setCatalog] = useState<CloudflareCatalog | null>(null);
  const [error, setError] = useState<unknown>(null);

  // Typing asks Cloudflare once the person pauses, not on every key.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchText.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    let current = true;
    const params = new URLSearchParams({ task, search, page: String(page), per_page: String(PER_PAGE) });
    api<CloudflareCatalog>(`/admin/control/cloudflare/models?${params}`)
      .then((body) => {
        if (!current) return;
        setCatalog(body);
        setError(null);
      })
      .catch((err: unknown) => {
        if (current) setError(err);
      });
    return () => {
      current = false;
    };
  }, [task, search, page, overview.account_id]);

  const rows = (catalog?.models ?? []).filter(
    (row) =>
      (plan === "any" || (plan === "paid") === row.paid_only) && (!vision || row.vision) && (!usable || row.can_add),
  );
  const pages = catalog?.total ? Math.max(1, Math.ceil(catalog.total / catalog.per_page)) : null;
  const hasNext = catalog ? (pages !== null ? page < pages : catalog.models.length >= catalog.per_page) : false;

  return (
    <section aria-labelledby="cf-models" className="flex flex-col gap-2" data-testid="cf-models">
      <h3 id="cf-models" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">
        {t("settings.cloudflare.models.title")}
      </h3>
      <p className="text-[12.5px] text-muted">{t("settings.cloudflare.models.intro")}</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field>
          {t("settings.cloudflare.models.search")}
          <Input
            type="search"
            dir="ltr"
            autoComplete="off"
            spellCheck={false}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </Field>
        <Field>
          {t("settings.cloudflare.models.task")}
          <Select
            value={task}
            onChange={(e) => {
              setTask(e.target.value);
              setPage(1);
            }}
          >
            {TASKS.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
            <option value="">{t("settings.cloudflare.models.taskAll")}</option>
          </Select>
        </Field>
        <Field>
          {t("settings.cloudflare.models.plan")}
          <Select value={plan} onChange={(e) => setPlan(e.target.value as Plan)}>
            <option value="any">{t("settings.cloudflare.models.planAny")}</option>
            <option value="free">{t("settings.cloudflare.models.planFree")}</option>
            <option value="paid">{t("settings.cloudflare.models.planPaid")}</option>
          </Select>
        </Field>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2 text-[13px]">
        <label className="flex items-center gap-2">
          <input type="checkbox" className="size-4 accent-brand" checked={vision} onChange={(e) => setVision(e.target.checked)} />
          {t("settings.cloudflare.models.vision")}
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" className="size-4 accent-brand" checked={usable} onChange={(e) => setUsable(e.target.checked)} />
          {t("settings.cloudflare.models.usable")}
        </label>
      </div>
      <p className="text-[12px] text-faint">{t("settings.cloudflare.models.filterNote")}</p>
      {!overview.gateway_ready ? (
        <p className="text-[12.5px] text-muted" data-testid="cf-models-need-railway">{t("settings.cloudflare.models.needRailway")}</p>
      ) : null}
      {error ? <Alert className="mb-0" data-testid="cf-models-error">{errorText(error)}</Alert> : null}
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.cloudflare.models.col.model")}</TableHead>
              <TableHead>{t("settings.cloudflare.models.col.context")}</TableHead>
              <TableHead>{t("settings.cloudflare.models.col.price")}</TableHead>
              <TableHead>{t("settings.cloudflare.models.col.features")}</TableHead>
              <TableHead>{t("settings.cloudflare.models.col.aura")}</TableHead>
              <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {catalog === null && !error ? (
              <TableRow><TableCell colSpan={6}><Skeleton className="h-6 w-full" /></TableCell></TableRow>
            ) : null}
            {catalog !== null && rows.length === 0 ? (
              <TableEmpty colSpan={6}>{t("settings.cloudflare.models.empty")}</TableEmpty>
            ) : null}
            {rows.map((row) => {
              const refusal = refusalKey(row.refusal);
              return (
                <TableRow key={row.model} data-testid={`cf-model-${row.model}`}>
                  <TableCell className="max-w-[22rem]">
                    <code dir="ltr" className="text-[12.5px] font-semibold break-all">{row.model}</code>
                    {row.description ? <p className="mt-0.5 line-clamp-2 text-[12px] text-muted">{row.description}</p> : null}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted">
                    {row.context_window ? <bdi dir="ltr">{row.context_window.toLocaleString("en")}</bdi> : "—"}
                  </TableCell>
                  <TableCell className="text-[12px] text-muted">
                    {row.price.length === 0 ? (
                      "—"
                    ) : (
                      <ul className="flex flex-col">
                        {row.price.map((entry, index) => (
                          <li key={index}><bdi dir="ltr">{price(entry)}</bdi></li>
                        ))}
                      </ul>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {row.vision ? <Badge tone="brand">{t("settings.cloudflare.models.badge.vision")}</Badge> : null}
                      {row.reasoning ? <Badge>{t("settings.cloudflare.models.badge.reasoning")}</Badge> : null}
                      {row.function_calling ? <Badge>{t("settings.cloudflare.models.badge.functions")}</Badge> : null}
                      {row.paid_only ? <Badge tone="warn">{t("settings.cloudflare.models.badge.paid")}</Badge> : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-[12.5px]">
                    {row.can_add ? (
                      <Badge tone="ok">{t("settings.cloudflare.models.canUse")}</Badge>
                    ) : (
                      <span className="text-muted" data-testid="cf-model-refusal">
                        {refusal ? t(refusal) : row.refusal}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-end">
                    {row.can_add ? (
                      <Button
                        size="sm"
                        disabled={!overview.gateway_ready}
                        aria-label={t("settings.cloudflare.models.addFor", { model: row.model })}
                        onClick={() => onAdd(row)}
                      >
                        <Plus aria-hidden="true" />
                        {t("settings.cloudflare.models.add")}
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>
      {catalog ? (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-muted" data-testid="cf-models-pager">
          <span>
            {pages !== null
              ? t("settings.cloudflare.models.page", { page, pages })
              : t("settings.cloudflare.models.pageOnly", { page })}
            {catalog.total !== null && catalog.total !== undefined ? ` · ${t("settings.cloudflare.models.pageTotal", { total: catalog.total })}` : ""}
          </span>
          <span className="flex gap-1.5">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage((n) => Math.max(1, n - 1))}>
              {t("settings.cloudflare.models.previous")}
            </Button>
            <Button size="sm" disabled={!hasNext} onClick={() => setPage((n) => n + 1)}>
              {t("settings.cloudflare.models.next")}
            </Button>
          </span>
        </div>
      ) : null}
    </section>
  );
}
