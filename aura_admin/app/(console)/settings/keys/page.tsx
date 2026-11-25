"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { KeyRound, RefreshCw } from "lucide-react";

import { stepUpError } from "@/components/change-tray";
import { CloudflareTokens } from "@/components/cloudflare-tokens";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { ChangeRow, KeySlotRow, KeysOverview } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

function SlotActions({ slot, ready, onDone }: { slot: KeySlotRow; ready: boolean; onDone: (message: string) => void }) {
  const { t } = useI18n();
  const stepUp = useStepUp();
  const [value, setValue] = useState("");

  async function replace() {
    try {
      await stepUp.write<ChangeRow>(`/admin/control/keys/${slot.name}/replace`, { value, confirm: slot.name });
      onDone(t("settings.keys.replaced", { name: slot.name }));
    } catch (err) {
      throw stepUpError(err, t);
    } finally {
      setValue("");
    }
  }

  async function rotate() {
    try {
      await stepUp.write<ChangeRow>(`/admin/control/keys/${slot.name}/rotate`, { confirm: slot.name });
      onDone(t(slot.rotation === "both_sides" ? "settings.keys.rotatedBoth" : "settings.keys.created", { name: slot.name }));
    } catch (err) {
      throw stepUpError(err, t);
    }
  }

  return (
    <div className="flex flex-wrap justify-end gap-1">
      {slot.rotation === "both_sides" ? (
        <ConfirmDialog
          title={t("settings.keys.rotateBothTitle", { name: slot.name })}
          confirmText={slot.name}
          disabled={!ready}
          onConfirm={rotate}
          body={<p className="text-[13px] text-muted">{t("settings.keys.rotateBothBody")}</p>}
        >
          <RefreshCw aria-hidden="true" />{t("settings.keys.rotateBoth")}
        </ConfirmDialog>
      ) : null}
      {slot.rotation === "create" ? (
        <ConfirmDialog
          title={t("settings.keys.createTitle", { name: slot.name })}
          confirmText={slot.name}
          disabled={!ready}
          onConfirm={rotate}
          body={<p className="text-[13px] text-muted">{t("settings.keys.createBody")}</p>}
        >
          <RefreshCw aria-hidden="true" />{t("settings.keys.create")}
        </ConfirmDialog>
      ) : null}
      <ConfirmDialog
        title={t("settings.keys.replaceTitle", { name: slot.name })}
        confirmText={slot.name}
        disabled={!ready}
        label={t("settings.keys.replaceFor", { name: slot.name })}
        onConfirm={replace}
        body={
          <div className="flex flex-col gap-2">
            <Field>
              {t("settings.keys.newKey")}
              <Input dir="ltr" type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} />
            </Field>
            <p className="text-[12px] text-faint">{t("settings.keys.replaceBody", { variable: slot.api_key_env })}</p>
          </div>
        }
      >
        <KeyRound aria-hidden="true" />{t("settings.keys.replace")}
      </ConfirmDialog>
    </div>
  );
}

export default function KeysPage() {
  const { t, errorText } = useI18n();
  const [overview, setOverview] = useState<KeysOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(() => {
    api<KeysOverview>("/admin/control/keys")
      .then((body) => {
        setOverview(body);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  function done(message: string) {
    setNotice(message);
    setRefreshKey((n) => n + 1);
    load();
  }

  if (error && !overview) return <Alert>{errorText(error)}</Alert>;
  if (!overview) return <Skeleton className="h-40 w-full" />;
  return (
    <div className="flex flex-col gap-4" data-testid="keys-panel">
      {!overview.gateway_ready ? (
        <Alert className="mb-0" data-testid="keys-need-railway">
          {t("settings.keys.needRailway")}{" "}
          <Link className="underline" href="/settings">{t("settings.tab.overview")}</Link>
        </Alert>
      ) : (
        <p className="text-[13px] text-muted">{t("settings.keys.note")}</p>
      )}
      {notice ? <p className="text-[13px] text-ok" role="status" data-testid="keys-notice">{notice}</p> : null}
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.keys.provider")}</TableHead>
              <TableHead>{t("settings.keys.variable")}</TableHead>
              <TableHead>{t("common.status")}</TableHead>
              <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {overview.slots.length === 0 ? <TableEmpty colSpan={4}>{t("settings.keys.noSlots")}</TableEmpty> : null}
            {overview.slots.map((slot) => (
              <TableRow key={slot.name} data-testid={`key-${slot.name}`}>
                <TableCell className="align-top">
                  <div className="font-semibold">{slot.name}</div>
                  <code dir="ltr" className="text-[12px] text-muted">{slot.host || slot.kind}</code>
                </TableCell>
                <TableCell className="align-top"><code dir="ltr" className="break-all text-[12.5px]">{slot.api_key_env}</code></TableCell>
                <TableCell className="align-top">
                  <div className="flex flex-wrap gap-1">
                    {slot.key_set === null ? null : (
                      <Badge tone={slot.key_set ? "ok" : "warn"}>{slot.key_set ? t("settings.aura.set") : t("settings.keys.notSet")}</Badge>
                    )}
                    {slot.healthy === null || slot.healthy === undefined ? null : (
                      <Badge tone={slot.healthy ? "ok" : "bad"} dot data-testid="key-health">
                        {slot.healthy ? t("settings.keys.healthy") : t("settings.keys.unhealthy")}
                      </Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell className="align-top">
                  <SlotActions slot={slot} ready={overview.gateway_ready} onDone={done} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {overview.cloudflare_ready ? <CloudflareTokens refreshKey={refreshKey} onDone={done} /> : null}
    </div>
  );
}
