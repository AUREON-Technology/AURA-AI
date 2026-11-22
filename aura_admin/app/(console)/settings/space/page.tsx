"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { KeyRound, Lock, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import { ChangeTray, stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow, TableSkeleton } from "@/components/ui/table";
import { AuraApiError, api } from "@/lib/api";
import {
  isSecretVariable,
  spaceTone,
  type ChangeRow,
  type PendingChanges,
  type SpaceOverview,
  type SpaceSecret,
  type SpaceVariable,
} from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

function sleepLabel(seconds: number | null, t: (key: MessageKey, vars?: Record<string, string | number>) => string): string {
  if (seconds === null || seconds < 0) return t("settings.space.sleepNever");
  if (seconds < 3600) return t("settings.space.sleepMinutes", { n: Math.round(seconds / 60) });
  return t("settings.space.sleepHours", { n: Math.round(seconds / 3600) });
}

function VariableRow({ row, pending, onStage }: { row: SpaceVariable; pending: boolean; onStage: (name: string, value: string | null) => void }) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.value ?? "");
  return (
    <TableRow data-testid={`space-var-${row.name}`}>
      <TableCell className="align-top"><code dir="ltr" className="break-all text-[12.5px] font-semibold">{row.name}</code></TableCell>
      <TableCell className="min-w-0 align-top">
        {editing ? (
          <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); onStage(row.name, draft); setEditing(false); }}>
            <Input dir="ltr" aria-label={t("settings.railway.newValue", { name: row.name })} autoComplete="off" spellCheck={false}
              className="min-w-40 flex-1" value={draft} onChange={(e) => setDraft(e.target.value)} />
            <Button type="submit" size="sm">{t("settings.railway.stage")}</Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>{t("common.cancel")}</Button>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            {row.secret ? (
              <Badge data-testid="secret-badge"><Lock aria-hidden="true" className="size-3" />{t("settings.aura.secret")}</Badge>
            ) : (
              <code dir="ltr" className="max-w-full break-all rounded bg-surface-variant px-1.5 py-0.5">{row.value === "" ? '""' : row.value}</code>
            )}
            {pending ? <Badge tone="warn">{t("settings.railway.pending")}</Badge> : null}
          </div>
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap text-end align-top">
        {!editing && !row.secret ? (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="icon" aria-label={t("settings.railway.edit", { name: row.name })} onClick={() => setEditing(true)}><Pencil aria-hidden="true" /></Button>
            <Button variant="ghost" size="icon" aria-label={t("settings.railway.delete", { name: row.name })} onClick={() => onStage(row.name, null)}><Trash2 aria-hidden="true" /></Button>
          </div>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

function SecretRow({ row, spaceName, onDone }: { row: SpaceSecret; spaceName: string; onDone: (change: ChangeRow, message: string) => void }) {
  const { t, lang } = useI18n();
  const stepUp = useStepUp();
  const [value, setValue] = useState("");

  async function write(next: string | null) {
    try {
      const change = await stepUp.write<ChangeRow>("/admin/control/space/secrets", { changes: { [row.name]: next }, confirm: spaceName });
      onDone(change, t(next === null ? "settings.space.secretDeleted" : "settings.space.secretReplaced", { name: row.name }));
    } catch (err) {
      throw stepUpError(err, t);
    } finally {
      setValue("");
    }
  }

  return (
    <TableRow data-testid={`space-secret-${row.name}`}>
      <TableCell><code dir="ltr" className="text-[12.5px] font-semibold">{row.name}</code></TableCell>
      <TableCell className="text-muted">
        {row.updated_at ? t("settings.space.secretUpdated", { when: new Date(row.updated_at).toLocaleString(lang === "ar" ? "ar" : "en") }) : "—"}
      </TableCell>
      <TableCell className="whitespace-nowrap text-end">
        <div className="flex justify-end gap-1">
          <ConfirmDialog
            title={t("settings.space.replaceSecretTitle", { name: row.name })}
            confirmText={spaceName}
            label={t("settings.space.replaceSecret", { name: row.name })}
            onConfirm={() => write(value)}
            body={
              <div className="flex flex-col gap-2">
                <Field>
                  {t("settings.space.secretValue")}
                  <Input dir="ltr" type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} />
                </Field>
                <p className="text-[12px] text-warn">{t("settings.space.secretNoUndo")}</p>
              </div>
            }
          >
            <KeyRound aria-hidden="true" />{t("settings.space.replace")}
          </ConfirmDialog>
          <ConfirmDialog
            title={t("settings.space.deleteSecretTitle", { name: row.name })}
            confirmText={spaceName}
            danger
            label={t("settings.railway.delete", { name: row.name })}
            onConfirm={() => write(null)}
            body={<p className="text-[12px] text-warn">{t("settings.space.secretNoUndo")}</p>}
          >
            <Trash2 aria-hidden="true" />
          </ConfirmDialog>
        </div>
      </TableCell>
    </TableRow>
  );
}

function AddSecret({ spaceName, onDone }: { spaceName: string; onDone: (change: ChangeRow, message: string) => void }) {
  const { t } = useI18n();
  const stepUp = useStepUp();
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  return (
    <ConfirmDialog
      title={t("settings.space.addSecretTitle")}
      confirmText={spaceName}
      onConfirm={async () => {
        try {
          const change = await stepUp.write<ChangeRow>("/admin/control/space/secrets", { changes: { [name.trim()]: value }, confirm: spaceName });
          onDone(change, t("settings.space.secretReplaced", { name: name.trim() }));
        } catch (err) {
          throw stepUpError(err, t);
        } finally {
          setValue("");
        }
      }}
      body={
        <div className="flex flex-col gap-2">
          <Field>
            {t("settings.space.secretName")}
            <Input dir="ltr" autoComplete="off" spellCheck={false} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field>
            {t("settings.space.secretValue")}
            <Input dir="ltr" type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
        </div>
      }
    >
      <Plus aria-hidden="true" />{t("settings.space.addSecret")}
    </ConfirmDialog>
  );
}

function Logs() {
  const { t, errorText } = useI18n();
  const [kind, setKind] = useState<"run" | "build">("run");
  const [lines, setLines] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLines(null);
    setError(null);
    api<{ lines: string[] }>(`/admin/control/space/logs?kind=${kind}&limit=300`)
      .then((body) => setLines(body.lines))
      .catch((err: unknown) => setError(err));
  }, [kind]);

  useEffect(load, [load]);

  return (
    <Card className="flex flex-col gap-2 p-4" data-testid="space-logs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[14px] font-semibold">{t("settings.railway.logsTitle")}</h3>
        <div className="flex gap-1">
          {(["run", "build"] as const).map((k) => (
            <Button key={k} size="sm" variant={kind === k ? "default" : "ghost"} aria-pressed={kind === k} onClick={() => setKind(k)}>
              {t(k === "run" ? "settings.space.logsRun" : "settings.railway.logsBuild")}
            </Button>
          ))}
          <Button size="icon" variant="ghost" aria-label={t("settings.railway.refresh")} onClick={load}><RefreshCw aria-hidden="true" /></Button>
        </div>
      </div>
      {error ? <Alert className="mb-0">{errorText(error)}</Alert> : null}
      {lines === null && !error ? <Skeleton className="h-32 w-full" /> : null}
      {lines ? (
        <pre dir="ltr" className="max-h-96 overflow-auto rounded bg-surface-variant p-3 text-[11.5px] leading-relaxed whitespace-pre-wrap break-all">
          {lines.length ? lines.join("\n") : t("settings.railway.noLogs")}
        </pre>
      ) : null}
    </Card>
  );
}

export default function SpacePage() {
  const { t, errorText } = useI18n();
  const stepUp = useStepUp();
  const [space, setSpace] = useState<SpaceOverview | null>(null);
  const [variables, setVariables] = useState<SpaceVariable[] | null>(null);
  const [secrets, setSecrets] = useState<SpaceSecret[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState<PendingChanges>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [hardware, setHardware] = useState("");
  const [sleep, setSleep] = useState("");
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");

  const load = useCallback(() => {
    Promise.all([
      api<SpaceOverview>("/admin/control/space"),
      api<{ variables: SpaceVariable[] }>("/admin/control/space/variables"),
      api<{ secrets: SpaceSecret[] }>("/admin/control/space/secrets"),
    ])
      .then(([s, v, k]) => {
        setSpace(s);
        setVariables(v.variables);
        setSecrets(k.secrets);
        // Only the first load fills the pickers: a reload after an action must not undo a
        // choice the person made meanwhile.
        setHardware((current) => current || (s.requested_hardware ?? s.hardware ?? ""));
        setSleep((current) => current || String(s.sleep_time ?? -1));
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  const secretNames = useMemo(() => new Set((variables ?? []).filter((v) => v.secret).map((v) => v.name)), [variables]);

  function done(_change: ChangeRow, message: string) {
    setNotice(message);
    setPending({});
    load();
  }

  async function act(run: () => Promise<ChangeRow>, message: string) {
    try {
      done(await run(), message);
    } catch (err) {
      throw stepUpError(err, t);
    }
  }

  if (error && !space) {
    const notConnected = error instanceof AuraApiError && error.status === 404;
    return notConnected ? (
      <Alert data-testid="space-not-connected">
        {t("settings.space.notConnected")}{" "}
        <Link className="underline" href="/settings">{t("settings.tab.overview")}</Link>
      </Alert>
    ) : (
      <Alert>{errorText(error)}</Alert>
    );
  }
  if (!space) return <Skeleton className="h-40 w-full" />;
  const name = space.name;
  const paused = ["PAUSED", "STOPPED"].includes(space.stage.toUpperCase());
  const hardwareChanged = hardware && hardware !== (space.requested_hardware ?? space.hardware);
  const sleepChanged = sleep !== String(space.sleep_time ?? -1);

  return (
    <div className="flex flex-col gap-4" data-testid="space-panel">
      <Card className="flex flex-col gap-3 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[16px] font-semibold" dir="ltr">{space.space_id}</h2>
          <Badge tone={spaceTone(space.stage)} dot data-testid="space-stage">{space.stage}</Badge>
          {space.change_in_progress ? <Badge tone="warn" pulse>{t("settings.railway.watching")}</Badge> : null}
        </div>
        <p className="text-[13px] text-muted">
          {t("settings.space.runtime", {
            hardware: space.hardware ?? "—",
            sleep: sleepLabel(space.sleep_time, t),
          })}
          {space.requested_hardware && space.requested_hardware !== space.hardware
            ? ` · ${t("settings.space.requested", { hardware: space.requested_hardware })}`
            : ""}
        </p>
        {notice ? <p className="text-[13px] text-ok" role="status" data-testid="space-notice">{notice}</p> : null}
        <div className="flex flex-wrap gap-2">
          {paused ? (
            <ConfirmDialog title={t("settings.space.resumeTitle", { name })} confirmText={name}
              onConfirm={() => act(() => stepUp.write<ChangeRow>("/admin/control/space/restart", { confirm: name }), t("settings.space.resumed"))}>
              {t("settings.space.resume")}
            </ConfirmDialog>
          ) : (
            <ConfirmDialog title={t("settings.space.pauseTitle", { name })} confirmText={name} danger
              body={<p className="text-[13px] text-muted">{t("settings.space.pauseBody")}</p>}
              onConfirm={() => act(() => stepUp.write<ChangeRow>("/admin/control/space/pause", { confirm: name }), t("settings.space.paused"))}>
              {t("settings.space.pause")}
            </ConfirmDialog>
          )}
          {!paused ? (
            <ConfirmDialog title={t("settings.space.restartTitle", { name })} confirmText={name}
              onConfirm={() => act(() => stepUp.write<ChangeRow>("/admin/control/space/restart", { confirm: name }), t("settings.space.restarted"))}>
              {t("settings.railway.restart")}
            </ConfirmDialog>
          ) : null}
          <ConfirmDialog title={t("settings.space.rebuildTitle", { name })} confirmText={name} danger
            body={<p className="text-[13px] text-muted">{t("settings.space.rebuildBody")}</p>}
            onConfirm={() => act(() => stepUp.write<ChangeRow>("/admin/control/space/restart", { confirm: name, factory: true }), t("settings.space.rebuilt"))}>
            {t("settings.space.rebuild")}
          </ConfirmDialog>
        </div>
      </Card>

      <Card className="grid gap-4 p-5 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Field>
            {t("settings.space.hardware")}
            <Select dir="ltr" value={hardware} onChange={(e) => setHardware(e.target.value)}>
              {space.hardware_options.map((h) => <option key={h} value={h}>{h}</option>)}
            </Select>
          </Field>
          <ConfirmDialog title={t("settings.space.hardwareTitle", { name, hardware })} confirmText={name} disabled={!hardwareChanged}
            body={<p className="text-[13px] text-warn">{t("settings.space.hardwareBody")}</p>}
            onConfirm={() => act(() => stepUp.write<ChangeRow>("/admin/control/space/hardware", { hardware, confirm: name }), t("settings.space.hardwareRequested", { hardware }))}>
            {t("settings.space.applyHardware")}
          </ConfirmDialog>
        </div>
        <div className="flex flex-col gap-2">
          <Field>
            {t("settings.space.sleep")}
            <Select value={sleep} onChange={(e) => setSleep(e.target.value)}>
              {space.sleep_choices.map((s) => <option key={s} value={String(s)}>{sleepLabel(s, t)}</option>)}
            </Select>
          </Field>
          <ConfirmDialog title={t("settings.space.sleepTitle", { name })} confirmText={name} disabled={!sleepChanged}
            onConfirm={() => act(() => stepUp.write<ChangeRow>("/admin/control/space/sleep-time", { seconds: Number(sleep), confirm: name }), t("settings.space.sleepSet"))}>
            {t("settings.space.applySleep")}
          </ConfirmDialog>
        </div>
      </Card>

      <ChangeTray
        serviceName={name}
        changes={pending}
        secretNames={secretNames}
        applyBody={t("settings.space.trayBody", { minutes: space.watch_minutes })}
        onRemove={(n) => setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== n)))}
        onClear={() => setPending({})}
        onCheck={(changes) => api("/admin/control/space/variables/validate", { method: "POST", body: JSON.stringify({ changes }) })}
        onApply={async (changes) => {
          try {
            return await stepUp.write<ChangeRow>("/admin/control/space/variables", { changes, confirm: name });
          } catch (err) {
            throw stepUpError(err, t);
          }
        }}
        onApplied={(change) => done(change, t("settings.space.applied", { minutes: space.watch_minutes }))}
      />

      <section aria-labelledby="space-vars" className="flex flex-col gap-2">
        <h3 id="space-vars" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">{t("settings.railway.variables")}</h3>
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
              {variables === null ? <TableSkeleton cols={3} /> : null}
              {variables?.length === 0 ? <TableEmpty colSpan={3}>{t("settings.railway.noVariables")}</TableEmpty> : null}
              {variables?.map((row) => (
                <VariableRow key={row.name} row={row} pending={row.name in pending} onStage={(n, v) => setPending((p) => ({ ...p, [n]: v }))} />
              ))}
            </TableBody>
          </Table>
        </Card>
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
          e.preventDefault();
          if (!newName.trim()) return;
          setPending((p) => ({ ...p, [newName.trim()]: newValue }));
          setNewName("");
          setNewValue("");
        }}>
          <Field className="min-w-40 flex-1">
            {t("settings.railway.addName")}
            <Input dir="ltr" autoComplete="off" spellCheck={false} value={newName} onChange={(e) => setNewName(e.target.value)} />
          </Field>
          <Field className="min-w-40 flex-[2]">
            {t("settings.railway.addValue")}
            <Input dir="ltr" type={isSecretVariable(newName, newValue) ? "password" : "text"} autoComplete="off" spellCheck={false}
              value={newValue} onChange={(e) => setNewValue(e.target.value)} />
          </Field>
          <Button type="submit" size="md"><Plus aria-hidden="true" />{t("settings.railway.add")}</Button>
        </form>
      </section>

      <section aria-labelledby="space-secrets" className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 id="space-secrets" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">{t("settings.space.secrets")}</h3>
          <AddSecret spaceName={name} onDone={done} />
        </div>
        <p className="text-[12.5px] text-muted">{t("settings.space.secretsNote")}</p>
        <Card className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("settings.railway.name")}</TableHead>
                <TableHead>{t("settings.space.lastChanged")}</TableHead>
                <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {secrets === null ? <TableSkeleton cols={3} /> : null}
              {secrets?.length === 0 ? <TableEmpty colSpan={3}>{t("settings.space.noSecrets")}</TableEmpty> : null}
              {secrets?.map((row) => <SecretRow key={row.name} row={row} spaceName={name} onDone={done} />)}
            </TableBody>
          </Table>
        </Card>
      </section>

      <Logs />
    </div>
  );
}
