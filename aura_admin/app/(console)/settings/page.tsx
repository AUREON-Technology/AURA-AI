"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleCheck, CircleDashed, KeyRound } from "lucide-react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { ControlOverview, ControlPlatform } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

/** Platforms whose token is for one thing the person names (owner/name, a URL). */
const TARGET_LABELS: Partial<Record<ControlPlatform["platform"], MessageKey>> = {
  hf_space: "settings.target.hf_space",
  qdrant: "settings.target.qdrant",
  grafana: "settings.target.grafana",
};

function platformLabel(platform: string): MessageKey {
  return `settings.platform.${platform}` as MessageKey;
}

/** Turns the step-up popup's own failures into words; anything else is a gateway refusal. */
function stepUpError(err: unknown, t: (key: MessageKey) => string): unknown {
  if (err instanceof Error && err.message === "popup_blocked") return new Error(t("settings.stepUp.blocked"));
  if (err instanceof Error && err.message === "step_up_cancelled") return new Error(t("settings.stepUp.cancelled"));
  return err;
}

function PlatformCard({ row, vaultReady, onChanged }: { row: ControlPlatform; vaultReady: boolean; onChanged: () => void }) {
  const { t, lang } = useI18n();
  const stepUp = useStepUp();
  const [token, setToken] = useState("");
  const [target, setTarget] = useState(row.target);
  const name = t(platformLabel(row.platform));
  const targetLabel = TARGET_LABELS[row.platform];
  const when = row.configured_at ? new Date(row.configured_at).toLocaleString(lang === "ar" ? "ar" : "en") : "";

  async function connect() {
    try {
      await stepUp.write(`/admin/control/platforms/${row.platform}/credential`, {
        token, target, confirm: row.platform,
      });
    } catch (err) {
      throw stepUpError(err, t);
    } finally {
      setToken("");
    }
    onChanged();
  }

  async function disconnect() {
    try {
      await stepUp.write(`/admin/control/platforms/${row.platform}/disconnect`, { confirm: row.platform });
    } catch (err) {
      throw stepUpError(err, t);
    }
    onChanged();
  }

  return (
    <Card className="flex flex-col gap-3 p-5" data-testid={`platform-${row.platform}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[15px] font-semibold">{name}</h2>
        {row.connected ? (
          <Badge tone={row.verified ? "ok" : "warn"} dot>
            {row.verified ? t("settings.verified") : t("settings.notVerified")}
          </Badge>
        ) : (
          <Badge>{t("settings.notConnected")}</Badge>
        )}
      </div>
      {row.connected ? (
        <div className="flex flex-col gap-1 text-[13px] text-muted">
          <span className="flex items-center gap-1.5">
            {row.verified ? <CircleCheck aria-hidden="true" className="size-3.5 text-ok" /> : <CircleDashed aria-hidden="true" className="size-3.5" />}
            {row.verified ? row.verified_detail : t("settings.notVerifiedHint")}
          </span>
          {row.target ? <code className="break-all text-[12px]">{row.target}</code> : null}
          {row.configured_by ? <span>{t("settings.configuredBy", { who: row.configured_by, when })}</span> : null}
        </div>
      ) : (
        <p className="text-[13px] text-muted">{row.token_hint}</p>
      )}
      <div className="mt-auto flex flex-wrap gap-2">
        <ConfirmDialog
          title={t("settings.connectTitle", { platform: name })}
          confirmText={row.platform}
          disabled={!vaultReady}
          onConfirm={connect}
          body={
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Field>
                  {t("settings.tokenLabel")}
                  <Input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    aria-describedby={`token-help-${row.platform}`}
                  />
                </Field>
                <span id={`token-help-${row.platform}`} className="flex items-center gap-1 text-[12px] text-faint">
                  <KeyRound aria-hidden="true" className="size-3" />
                  {t("settings.tokenHelp")}
                </span>
              </div>
              {targetLabel ? (
                <Field>
                  {t(targetLabel)}
                  <Input dir="ltr" autoComplete="off" spellCheck={false} value={target} onChange={(e) => setTarget(e.target.value)} />
                </Field>
              ) : null}
              <p className="text-[12px] text-faint">{row.token_hint}</p>
            </div>
          }
        >
          {row.connected ? t("settings.replaceToken") : t("settings.connect")}
        </ConfirmDialog>
        {row.connected ? (
          <ConfirmDialog
            title={t("settings.disconnectTitle", { platform: name })}
            confirmText={row.platform}
            danger
            onConfirm={disconnect}
          >
            {t("settings.disconnect")}
          </ConfirmDialog>
        ) : null}
      </div>
    </Card>
  );
}

export default function SettingsOverview() {
  const { t, errorText } = useI18n();
  const [overview, setOverview] = useState<ControlOverview | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    api<ControlOverview>("/admin/control/overview")
      .then((body) => {
        setOverview(body);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  if (error && !overview) {
    return <Alert>{errorText(error)}</Alert>;
  }
  if (!overview) {
    return <Skeleton className="h-40 w-full" />;
  }
  const revision = String(overview.release.revision ?? overview.release.git_sha ?? "?");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
        <span data-testid="settings-release">{t("settings.release", { revision, environment: overview.environment })}</span>
        <Badge tone={overview.open_changes ? "warn" : "neutral"}>{t("settings.openChanges", { count: overview.open_changes })}</Badge>
      </div>
      {!overview.vault_ready ? <Alert data-testid="vault-missing">{t("settings.vaultMissing")}</Alert> : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {overview.platforms.map((row) => (
          <PlatformCard key={row.platform} row={row} vaultReady={overview.vault_ready} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
