"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Lock, Pencil, Plus, RefreshCw, ScrollText, Trash2 } from "lucide-react";

import { ChangeTray, stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { AuraApiError, api } from "@/lib/api";
import {
  deploymentTone,
  isSecretVariable,
  type ChangeRow,
  type LogRow,
  type PendingChanges,
  type RailwayDeployment,
  type RailwayOverview,
  type RailwayServiceRow,
  type RailwayVariable,
} from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import { cn } from "@/lib/utils";

function when(value: string | null, lang: string): string {
  return value ? new Date(value).toLocaleString(lang === "ar" ? "ar" : "en") : "—";
}

function VariableRow({
  row,
  pending,
  onStage,
}: {
  row: RailwayVariable;
  pending: boolean;
  onStage: (name: string, value: string | null) => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.value ?? "");
  const locked = row.protected || row.provided || row.reference;
  return (
    <TableRow data-testid={`var-${row.name}`}>
      <TableCell className="align-top">
        <code dir="ltr" className="break-all text-[12.5px] font-semibold">{row.name}</code>
      </TableCell>
      <TableCell className="min-w-0 align-top">
        {editing ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              onStage(row.name, draft);
              setEditing(false);
              if (row.secret) setDraft("");
            }}
          >
            <Input
              dir="ltr"
              aria-label={t("settings.railway.newValue", { name: row.name })}
              type={row.secret ? "password" : "text"}
              autoComplete="off"
              spellCheck={false}
              className="min-w-40 flex-1"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button type="submit" size="sm">{t("settings.railway.stage")}</Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>{t("common.cancel")}</Button>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            {row.secret ? (
              <Badge data-testid="secret-badge"><Lock aria-hidden="true" className="size-3" />{t("settings.aura.secret")} · {t("settings.aura.set")}</Badge>
            ) : (
              <code dir="ltr" className="max-w-full break-all rounded bg-surface-variant px-1.5 py-0.5">{row.value === "" ? '""' : row.value}</code>
            )}
            {row.reference ? <Badge>{t("settings.railway.reference")}</Badge> : null}
            {row.protected ? <Badge tone="warn">{t("settings.aura.protected")}</Badge> : null}
            {row.provided ? <Badge>{t("settings.railway.provided")}</Badge> : null}
            {pending ? <Badge tone="warn">{t("settings.railway.pending")}</Badge> : null}
          </div>
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap text-end align-top">
        {!locked && !editing ? (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="icon" aria-label={t("settings.railway.edit", { name: row.name })} onClick={() => setEditing(true)}>
              <Pencil aria-hidden="true" />
            </Button>
            <Button variant="ghost" size="icon" aria-label={t("settings.railway.delete", { name: row.name })} onClick={() => onStage(row.name, null)}>
              <Trash2 aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

function AddVariable({ onStage }: { onStage: (name: string, value: string) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const secret = isSecretVariable(name, value);
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        onStage(name.trim(), value);
        setName("");
        setValue("");
      }}
    >
      <Field className="min-w-40 flex-1">
        {t("settings.railway.addName")}
        <Input dir="ltr" autoComplete="off" spellCheck={false} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field className="min-w-40 flex-[2]">
        {t("settings.railway.addValue")}
        <Input dir="ltr" type={secret ? "password" : "text"} autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} />
      </Field>
      <Button type="submit" size="md"><Plus aria-hidden="true" />{t("settings.railway.add")}</Button>
    </form>
  );
}

function Logs({ serviceId, deploymentId, onClose }: { serviceId: string; deploymentId: string; onClose: () => void }) {
  const { t, errorText } = useI18n();
  const [kind, setKind] = useState<"deploy" | "build">("deploy");
  const [lines, setLines] = useState<LogRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLines(null);
    setError(null);
    api<{ lines: LogRow[] }>(`/admin/control/railway/services/${serviceId}/deployments/${deploymentId}/logs?kind=${kind}&limit=300`)
      .then((body) => setLines(body.lines))
      .catch((err: unknown) => setError(err));
  }, [serviceId, deploymentId, kind]);

  useEffect(load, [load]);

  return (
    <Card className="flex flex-col gap-2 p-4" data-testid="logs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[14px] font-semibold">{t("settings.railway.logsTitle")} <code dir="ltr" className="text-[12px] text-muted">{deploymentId}</code></h3>
        <div className="flex gap-1">
          {(["deploy", "build"] as const).map((k) => (
            <Button key={k} size="sm" variant={kind === k ? "default" : "ghost"} aria-pressed={kind === k} onClick={() => setKind(k)}>
              {t(k === "deploy" ? "settings.railway.logsDeploy" : "settings.railway.logsBuild")}
            </Button>
          ))}
          <Button size="icon" variant="ghost" aria-label={t("settings.railway.refresh")} onClick={load}><RefreshCw aria-hidden="true" /></Button>
          <Button size="sm" variant="ghost" onClick={onClose}>{t("settings.railway.close")}</Button>
        </div>
      </div>
      {error ? <Alert className="mb-0">{errorText(error)}</Alert> : null}
      {lines === null && !error ? <Skeleton className="h-32 w-full" /> : null}
      {lines ? (
        <pre dir="ltr" className="max-h-96 overflow-auto rounded bg-surface-variant p-3 text-[11.5px] leading-relaxed whitespace-pre-wrap break-all">
          {lines.length ? lines.map((l) => `${l.timestamp} ${l.severity ? `[${l.severity}] ` : ""}${l.message}`).join("\n") : t("settings.railway.noLogs")}
        </pre>
      ) : null}
    </Card>
  );
}

function ServicePanel({ service, onChanged }: { service: RailwayServiceRow; onChanged: () => void }) {
  const { t, lang, errorText } = useI18n();
  const stepUp = useStepUp();
  const [variables, setVariables] = useState<RailwayVariable[] | null>(null);
  const [deployments, setDeployments] = useState<RailwayDeployment[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState<PendingChanges>({});
  const [logsFor, setLogsFor] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const id = service.service_id;

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      api<{ variables: RailwayVariable[] }>(`/admin/control/railway/services/${id}/variables`),
      api<{ deployments: RailwayDeployment[] }>(`/admin/control/railway/services/${id}/deployments?limit=20`),
    ])
      .then(([v, d]) => {
        setVariables(v.variables);
        setDeployments(d.deployments);
      })
      .catch((err: unknown) => setError(err));
  }, [id]);

  useEffect(() => {
    setVariables(null);
    setDeployments(null);
    setPending({});
    setLogsFor(null);
    setNotice(null);
    load();
  }, [load]);

  const secretNames = useMemo(() => new Set((variables ?? []).filter((v) => v.secret).map((v) => v.name)), [variables]);

  function stage(name: string, value: string | null) {
    setPending((p) => ({ ...p, [name]: value }));
  }

  function done(change: ChangeRow, message: string) {
    setNotice(message);
    setPending({});
    load();
    onChanged();
    return change;
  }

  async function action(path: "redeploy" | "restart") {
    try {
      const body = { confirm: service.name };
      const change =
        path === "redeploy"
          ? await stepUp.write<ChangeRow>(`/admin/control/railway/services/${id}/redeploy`, body)
          : await stepUp.write<ChangeRow>(`/admin/control/railway/services/${id}/restart`, body);
      done(change, t(path === "redeploy" ? "settings.railway.redeployed" : "settings.railway.restarted"));
    } catch (err) {
      throw stepUpError(err, t);
    }
  }

  async function rollback(deploymentId: string) {
    try {
      const change = await stepUp.write<ChangeRow>(`/admin/control/railway/services/${id}/rollback`, {
        deployment_id: deploymentId,
        confirm: service.name,
      });
      done(change, t("settings.railway.rolledBack"));
    } catch (err) {
      throw stepUpError(err, t);
    }
  }

  const latestId = deployments?.[0]?.deployment_id;

  return (
    <div className="flex flex-col gap-4" data-testid="service-panel">
      <Card className="flex flex-col gap-3 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[16px] font-semibold">{service.name}</h2>
          <Badge tone={deploymentTone(service.latest_status)} dot>{service.latest_status ?? "—"}</Badge>
          {service.is_gateway ? <Badge tone="ok">{t("settings.railway.thisGateway")}</Badge> : null}
          {service.change_in_progress ? <Badge tone="warn" pulse>{t("settings.railway.watching")}</Badge> : null}
        </div>
        {!service.healthcheck_path ? (
          <Alert className="mb-0" data-testid="no-healthcheck">{t("settings.railway.noHealthcheck")}</Alert>
        ) : (
          <p className="text-[13px] text-muted">{t("settings.railway.healthcheck", { path: service.healthcheck_path })}</p>
        )}
        {notice ? <p className="text-[13px] text-ok" role="status" data-testid="railway-notice">{notice}</p> : null}
        <div className="flex flex-wrap gap-2">
          <ConfirmDialog title={t("settings.railway.redeployTitle", { service: service.name })} confirmText={service.name} onConfirm={() => action("redeploy")}
            body={<p className="text-[13px] text-muted">{t("settings.railway.redeployBody")}</p>}>
            {t("settings.railway.redeploy")}
          </ConfirmDialog>
          <ConfirmDialog title={t("settings.railway.restartTitle", { service: service.name })} confirmText={service.name} danger onConfirm={() => action("restart")}>
            {t("settings.railway.restart")}
          </ConfirmDialog>
        </div>
      </Card>

      {error ? <Alert>{errorText(error)}</Alert> : null}

      <ChangeTray
        serviceId={id}
        serviceName={service.name}
        changes={pending}
        secretNames={secretNames}
        onRemove={(name) => setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== name)))}
        onClear={() => setPending({})}
        onApplied={(change) => done(change, t("settings.railway.applied"))}
      />

      <section aria-labelledby="vars-title" className="flex flex-col gap-2">
        <h3 id="vars-title" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">{t("settings.railway.variables")}</h3>
        <Card className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("settings.railway.name")}</TableHead>
                <TableHead>{t("settings.railway.value")}</TableHead>
                <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {variables === null && !error ? <TableSkeleton cols={3} /> : null}
              {variables?.length === 0 ? <TableEmpty colSpan={3}>{t("settings.railway.noVariables")}</TableEmpty> : null}
              {variables?.map((row) => (
                <VariableRow key={row.name} row={row} pending={row.name in pending} onStage={stage} />
              ))}
            </TableBody>
          </Table>
        </Card>
        <AddVariable onStage={stage} />
      </section>

      <section aria-labelledby="deploys-title" className="flex flex-col gap-2">
        <h3 id="deploys-title" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">{t("settings.railway.deployments")}</h3>
        <Card className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("common.status")}</TableHead>
                <TableHead>{t("settings.changes.when")}</TableHead>
                <TableHead>{t("settings.railway.commit")}</TableHead>
                <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {deployments === null && !error ? <TableSkeleton cols={4} /> : null}
              {deployments?.length === 0 ? <TableEmpty colSpan={4}>{t("settings.railway.noDeployments")}</TableEmpty> : null}
              {deployments?.map((d) => (
                <TableRow key={d.deployment_id} data-testid="deployment-row">
                  <TableCell><Badge tone={deploymentTone(d.status)}>{d.status}</Badge></TableCell>
                  <TableCell className="whitespace-nowrap">{when(d.created_at, lang)}</TableCell>
                  <TableCell className="min-w-0">
                    <code dir="ltr" className="text-[12px]">{d.commit || "—"}</code>
                    {d.message ? <span className="ms-2 text-muted">{d.message}</span> : null}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-end">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" onClick={() => setLogsFor(d.deployment_id)} aria-label={t("settings.railway.logsFor", { id: d.deployment_id })}>
                        <ScrollText aria-hidden="true" />{t("settings.railway.logs")}
                      </Button>
                      {d.can_rollback && d.deployment_id !== latestId ? (
                        <ConfirmDialog
                          title={t("settings.railway.rollbackTitle", { service: service.name })}
                          confirmText={service.name}
                          danger
                          label={t("settings.railway.rollbackTo", { id: d.deployment_id })}
                          onConfirm={() => rollback(d.deployment_id)}
                          body={<p className="text-[13px] text-muted">{t("settings.railway.rollbackBody")}</p>}
                        >
                          {t("settings.railway.rollback")}
                        </ConfirmDialog>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        {logsFor ? <Logs serviceId={id} deploymentId={logsFor} onClose={() => setLogsFor(null)} /> : null}
      </section>
    </div>
  );
}

export default function RailwayPage() {
  const { t, errorText } = useI18n();
  const [overview, setOverview] = useState<RailwayOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(() => {
    api<RailwayOverview>("/admin/control/railway")
      .then((body) => {
        setOverview(body);
        setError(null);
        setSelected((current) => current ?? body.services.find((s) => s.is_gateway)?.service_id ?? body.services[0]?.service_id ?? null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  if (error && !overview) {
    const notConnected = error instanceof AuraApiError && error.status === 404;
    return notConnected ? (
      <Alert data-testid="railway-not-connected">
        {t("settings.railway.notConnected")}{" "}
        <Link className="underline" href="/settings">{t("settings.tab.overview")}</Link>
      </Alert>
    ) : (
      <Alert>{errorText(error)}</Alert>
    );
  }
  if (!overview) return <Skeleton className="h-40 w-full" />;
  const service = overview.services.find((s) => s.service_id === selected) ?? null;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] text-muted" data-testid="railway-scope">
        {t("settings.railway.scope", { project: overview.project.name || overview.project.id, environment: overview.environment.name || overview.environment.id })}
      </p>
      <div role="tablist" aria-label={t("settings.railway.services")} className="flex flex-wrap gap-2">
        {overview.services.map((s) => (
          <button
            key={s.service_id}
            role="tab"
            type="button"
            aria-selected={s.service_id === selected}
            data-testid={`service-${s.name}`}
            onClick={() => setSelected(s.service_id)}
            className={cn(
              "flex items-center gap-2 rounded-sm border px-3 py-2 text-[13px] font-medium",
              s.service_id === selected ? "border-brand bg-surface-variant text-fg" : "border-line bg-surface text-muted hover:text-fg",
            )}
          >
            <span className={cn("size-2 rounded-full", {
              "bg-ok": deploymentTone(s.latest_status) === "ok",
              "bg-bad": deploymentTone(s.latest_status) === "bad",
              "bg-warn": deploymentTone(s.latest_status) === "warn",
              "bg-faint": deploymentTone(s.latest_status) === "neutral",
            })} aria-hidden="true" />
            {s.name}
          </button>
        ))}
      </div>
      {service ? <ServicePanel key={service.service_id} service={service} onChanged={load} /> : null}
    </div>
  );
}
