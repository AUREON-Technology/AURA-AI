"use client";

import { useState } from "react";
import { CircleCheck, ListChecks, X } from "lucide-react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { isSecretVariable, type ChangeRow, type PendingChanges } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

/** Turns the step-up popup's own failures into words; anything else is a gateway refusal. */
export function stepUpError(err: unknown, t: (key: "settings.stepUp.blocked" | "settings.stepUp.cancelled") => string): unknown {
  if (err instanceof Error && err.message === "popup_blocked") return new Error(t("settings.stepUp.blocked"));
  if (err instanceof Error && err.message === "step_up_cancelled") return new Error(t("settings.stepUp.cancelled"));
  return err;
}

/**
 * Variable edits wait here until they are applied together: check (a dry run on the gateway, which
 * also proves the gateway would still boot), then apply behind the service's name and a fresh
 * sign-in. One apply is one redeploy, watched for five minutes and rolled back if it goes wrong.
 * A secret's new value is never shown back, here or anywhere.
 */
export function ChangeTray({
  serviceName,
  changes,
  secretNames,
  onRemove,
  onClear,
  onApplied,
  onCheck,
  onApply,
  applyBody,
}: {
  /** What the person types to apply, and names the tray. */
  serviceName: string;
  changes: PendingChanges;
  secretNames: ReadonlySet<string>;
  onRemove: (name: string) => void;
  onClear: () => void;
  onApplied: (change: ChangeRow) => void;
  /** The dry run (no step-up: nothing is written). */
  onCheck: (changes: PendingChanges) => Promise<unknown>;
  /** The write, through the step-up helper. */
  onApply: (changes: PendingChanges) => Promise<ChangeRow>;
  applyBody?: string;
}) {
  const { t, errorText } = useI18n();
  const [checked, setChecked] = useState<"ok" | null>(null);
  const [checkError, setCheckError] = useState<unknown>(null);
  const [checking, setChecking] = useState(false);
  const names = Object.keys(changes).sort();
  if (names.length === 0) return null;

  async function check() {
    setChecking(true);
    setChecked(null);
    setCheckError(null);
    try {
      await onCheck(changes);
      setChecked("ok");
    } catch (err) {
      setCheckError(err);
    } finally {
      setChecking(false);
    }
  }

  async function apply() {
    try {
      onApplied(await onApply(changes));
    } catch (err) {
      throw stepUpError(err, t);
    }
  }

  return (
    <Card className="flex flex-col gap-3 border-brand/40 p-4" data-testid="change-tray">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-[14px] font-semibold">
          <ListChecks aria-hidden="true" className="size-4 text-brand" />
          {t("settings.tray.title", { count: names.length, service: serviceName })}
        </h3>
        <Button variant="ghost" size="sm" onClick={onClear}>
          {t("settings.tray.clear")}
        </Button>
      </div>
      <ul className="flex flex-col gap-1.5 text-[13px]">
        {names.map((name) => {
          const value = changes[name];
          const secret = secretNames.has(name) || isSecretVariable(name, value);
          return (
            <li key={name} className="flex min-w-0 items-center gap-2" data-testid="tray-row">
              <code dir="ltr" className="shrink-0 font-semibold">{name}</code>
              <span className="min-w-0 flex-1 truncate text-muted" dir="ltr">
                {value === null
                  ? t("settings.tray.delete")
                  : secret
                    ? t("settings.tray.secretSet")
                    : `→ ${value === "" ? '""' : value}`}
              </span>
              <Button variant="ghost" size="icon" aria-label={t("settings.tray.remove", { name })} onClick={() => onRemove(name)}>
                <X aria-hidden="true" />
              </Button>
            </li>
          );
        })}
      </ul>
      {checkError ? <Alert className="mb-0">{errorText(checkError)}</Alert> : null}
      {checked ? (
        <p className="flex items-center gap-1.5 text-[13px] text-ok" data-testid="tray-checked">
          <CircleCheck aria-hidden="true" className="size-4" />
          {t("settings.tray.checked")}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={check} disabled={checking}>
          {t("settings.tray.check")}
        </Button>
        <ConfirmDialog
          title={t("settings.tray.applyTitle", { service: serviceName })}
          confirmText={serviceName}
          onConfirm={apply}
          body={<p className="text-[13px] text-muted">{applyBody ?? t("settings.tray.applyBody")}</p>}
        >
          {t("settings.tray.apply")}
        </ConfirmDialog>
      </div>
    </Card>
  );
}
