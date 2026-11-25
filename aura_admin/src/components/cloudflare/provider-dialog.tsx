"use client";

import { useState } from "react";
import { LoaderCircle } from "lucide-react";

import { stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { api } from "@/lib/api";
import type {
  ChangeRow,
  CloudflareKeyMode,
  CloudflareOverview,
  CloudflareProviderBody,
  CloudflareProviderRow,
  CloudflareValidateResult,
} from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

import { problemText } from "./refusal";

/** What opens the dialog: a catalog model to add (or none), or an existing provider to replace. */
export type ProviderDraft =
  | { mode: "add"; model: string; contextWindow: number | null; vision: boolean }
  | { mode: "replace"; row: CloudflareProviderRow };

type Form = {
  name: string;
  model: string;
  tier: string;
  context: string;
  maxOutput: string;
  enabled: boolean;
  rpm: string;
  rpd: string;
  tpm: string;
  tpd: string;
  speed: string;
  json: boolean;
  vision: boolean;
  key: CloudflareKeyMode;
  keyValue: string;
  consent: boolean;
};

/** A provider name suggested from a model id: `@cf/qwen/qwen3-30b` becomes `cf-qwen3-30b`. */
export function suggestName(model: string): string {
  const last = model.split("/").pop() ?? "";
  const slug = last.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug ? `cf-${slug}`.slice(0, 32).replace(/-+$/, "") : "";
}

function initialForm(draft: ProviderDraft): Form {
  if (draft.mode === "replace") {
    const row = draft.row;
    return {
      name: row.name,
      model: row.model,
      tier: String(row.quality_tier ?? 2),
      context: String(row.context_window ?? 8192),
      maxOutput: String(row.max_output_tokens ?? 2048),
      enabled: row.enabled,
      rpm: String(row.rpm),
      rpd: String(row.rpd),
      tpm: String(row.tpm),
      tpd: String(row.tpd),
      speed: String(row.tokens_per_second),
      json: row.json_mode,
      vision: row.vision,
      key: "keep",
      keyValue: "",
      consent: false,
    };
  }
  const context = draft.contextWindow && draft.contextWindow > 0 ? draft.contextWindow : 8192;
  return {
    name: suggestName(draft.model),
    model: draft.model,
    tier: "2",
    context: String(context),
    maxOutput: String(Math.min(2048, context)),
    enabled: true,
    rpm: "0",
    rpd: "0",
    tpm: "0",
    tpd: "0",
    speed: "0",
    json: false,
    vision: draft.vision,
    key: "create",
    keyValue: "",
    consent: false,
  };
}

const whole = (value: string): number => (/^\d{1,10}$/.test(value.trim()) ? Number(value.trim()) : Number.NaN);
const decimal = (value: string): number => (/^\d{1,5}(\.\d{1,3})?$/.test(value.trim()) ? Number(value.trim()) : Number.NaN);

/** The request body without the key's value, or null while a number is not a number. Numbers are numbers: the gateway is strict. */
function toBody(form: Form, consenting: boolean): CloudflareProviderBody | null {
  const body: CloudflareProviderBody = {
    model: form.model.trim(),
    context_window: whole(form.context),
    quality_tier: whole(form.tier),
    max_output_tokens: whole(form.maxOutput),
    enabled: form.enabled,
    rpm: whole(form.rpm),
    rpd: whole(form.rpd),
    tpm: whole(form.tpm),
    tpd: whole(form.tpd),
    tokens_per_second: decimal(form.speed),
    json_mode: form.json,
    vision: form.vision,
    key: form.key,
    enable_consent: consenting,
  };
  const numbers = [body.context_window, body.quality_tier, body.max_output_tokens, body.rpm, body.rpd, body.tpm, body.tpd, body.tokens_per_second];
  return numbers.every(Number.isFinite) ? body : null;
}

/**
 * Add or Replace one Cloudflare provider. The form is checked first (a dry run on the gateway that
 * lists the variable names it would write and says whether it would accept them), and only a fresh
 * passing check unlocks Apply, which asks for the provider's name and a fresh sign-in. A pasted key
 * lives in a password field, is sent once with the apply and is never shown again.
 */
export function ProviderDialog({
  draft,
  overview,
  onClose,
  onDone,
}: {
  draft: ProviderDraft;
  overview: CloudflareOverview;
  onClose: () => void;
  onDone: (kind: "added" | "replaced", name: string) => void;
}) {
  const { t, errorText } = useI18n();
  const stepUp = useStepUp();
  const adding = draft.mode === "add";
  const [form, setForm] = useState<Form>(() => initialForm(draft));
  const [verdict, setVerdict] = useState<{ result: CloudflareValidateResult; signature: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const needsConsent = adding && overview.hosted_inference_allowed === false;
  const body = toBody(form, needsConsent && form.consent);
  const name = form.name.trim();
  const signature = JSON.stringify([name, form.model.trim(), body]);
  const fresh = verdict !== null && verdict.signature === signature;
  const keyMissing = form.key === "paste" && form.keyValue.trim() === "";
  const ready = fresh && body !== null && name !== "" && !keyMissing;

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  async function check() {
    if (!body) return;
    setChecking(true);
    setProblem(null);
    try {
      // The pasted key is not sent for a check: the gateway tests its settings with a stand-in.
      const result = await api<CloudflareValidateResult>("/admin/control/cloudflare/providers/validate", {
        method: "POST",
        body: JSON.stringify({ operation: adding ? "add" : "replace", name, ...body }),
      });
      setVerdict({ result, signature });
    } catch (err) {
      setVerdict(null);
      setProblem(problemText(err, t, errorText));
    } finally {
      setChecking(false);
    }
  }

  async function apply() {
    if (!body) return;
    const payload = { ...body, ...(form.key === "paste" ? { key_value: form.keyValue.trim() } : {}), confirm: name };
    try {
      if (adding) {
        await stepUp.write<ChangeRow>("/admin/control/cloudflare/providers", { ...payload, name });
      } else {
        await stepUp.write<ChangeRow>(`/admin/control/cloudflare/providers/${encodeURIComponent(name)}/replace`, payload);
      }
    } catch (err) {
      throw new Error(problemText(stepUpError(err, t), t, errorText));
    }
    onDone(adding ? "added" : "replaced", name);
  }

  const keyChoices: CloudflareKeyMode[] = adding ? ["create", "paste"] : ["keep", "create", "paste"];
  const numberProps = { type: "text", inputMode: "numeric", dir: "ltr", autoComplete: "off" } as const;

  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent
        className="top-[4vh] max-h-[92vh] w-[min(680px,calc(100vw-32px))] overflow-y-auto"
        data-testid="provider-dialog"
      >
        <DialogTitle className="break-words">
          {adding ? t("settings.cloudflare.providers.addTitle") : t("settings.cloudflare.providers.replaceTitle", { name })}
        </DialogTitle>
        <DialogDescription>{t("settings.cloudflare.providers.dialogBody")}</DialogDescription>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field className="sm:col-span-2">
            {t("settings.cloudflare.providers.field.model")}
            <Input dir="ltr" autoComplete="off" spellCheck={false} value={form.model} onChange={(e) => set("model", e.target.value)} />
          </Field>
          <Field className="sm:col-span-2">
            {t("settings.cloudflare.providers.field.name")}
            <Input
              dir="ltr"
              autoComplete="off"
              spellCheck={false}
              maxLength={32}
              value={form.name}
              disabled={!adding}
              onChange={(e) => set("name", e.target.value)}
            />
            <span className="text-[12px] font-normal text-faint">{t("settings.cloudflare.providers.field.nameHelp")}</span>
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.tier")}
            <Select value={form.tier} onChange={(e) => set("tier", e.target.value)}>
              <option value="1">{t("settings.cloudflare.providers.field.tier1")}</option>
              <option value="2">{t("settings.cloudflare.providers.field.tier2")}</option>
              <option value="3">{t("settings.cloudflare.providers.field.tier3")}</option>
            </Select>
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.speed")}
            <Input {...numberProps} inputMode="decimal" value={form.speed} onChange={(e) => set("speed", e.target.value)} />
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.context")}
            <Input {...numberProps} value={form.context} onChange={(e) => set("context", e.target.value)} />
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.maxOutput")}
            <Input {...numberProps} value={form.maxOutput} onChange={(e) => set("maxOutput", e.target.value)} />
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.rpm")}
            <Input {...numberProps} value={form.rpm} onChange={(e) => set("rpm", e.target.value)} />
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.rpd")}
            <Input {...numberProps} value={form.rpd} onChange={(e) => set("rpd", e.target.value)} />
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.tpm")}
            <Input {...numberProps} value={form.tpm} onChange={(e) => set("tpm", e.target.value)} />
          </Field>
          <Field>
            {t("settings.cloudflare.providers.field.tpd")}
            <Input {...numberProps} value={form.tpd} onChange={(e) => set("tpd", e.target.value)} />
          </Field>
          <p className="text-[12px] text-faint sm:col-span-2">{t("settings.cloudflare.providers.field.limitsHelp")}</p>
          <div className="flex flex-col gap-2 text-[13px] sm:col-span-2">
            <label className="flex items-center gap-2">
              <input type="checkbox" className="size-4 accent-brand" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              {t("settings.cloudflare.providers.field.enabled")}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" className="size-4 accent-brand" checked={form.json} onChange={(e) => set("json", e.target.checked)} />
              {t("settings.cloudflare.providers.field.json")}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" className="size-4 accent-brand" checked={form.vision} onChange={(e) => set("vision", e.target.checked)} />
              {t("settings.cloudflare.providers.field.vision")}
            </label>
            <p className="ps-6 text-[12px] text-faint" data-testid="vision-note">{t("settings.cloudflare.providers.field.visionNote")}</p>
          </div>

          <fieldset className="flex flex-col gap-2 text-[13px] sm:col-span-2">
            <legend className="mb-1 text-[12.5px] font-medium text-muted">{t("settings.cloudflare.providers.key.legend")}</legend>
            {keyChoices.map((choice) => (
              <label key={choice} className="flex items-start gap-2">
                <input
                  type="radio"
                  name="cf-key-mode"
                  className="mt-0.5 size-4 accent-brand"
                  checked={form.key === choice}
                  onChange={() => set("key", choice)}
                />
                {t(`settings.cloudflare.providers.key.${choice}` as MessageKey)}
              </label>
            ))}
            {form.key === "paste" ? (
              <Field>
                {t("settings.cloudflare.providers.key.value")}
                <Input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={form.keyValue}
                  onChange={(e) => set("keyValue", e.target.value)}
                />
                <span className="text-[12px] font-normal text-faint">{t("settings.cloudflare.providers.key.valueHelp")}</span>
              </Field>
            ) : null}
          </fieldset>

          {needsConsent ? (
            <div className="flex flex-col gap-2 rounded-sm border border-warn/40 bg-warn-soft px-3.5 py-3 text-[13px] sm:col-span-2" data-testid="consent-box">
              <strong>{t("settings.cloudflare.providers.consent.title")}</strong>
              <p>{t("settings.cloudflare.providers.consent.text")}</p>
              <label className="flex items-start gap-2 font-medium">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 accent-brand"
                  checked={form.consent}
                  onChange={(e) => set("consent", e.target.checked)}
                />
                {t("settings.cloudflare.providers.consent.check")}
              </label>
            </div>
          ) : null}
        </div>

        {body === null ? (
          <p className="mt-3 text-[12.5px] text-muted" data-testid="numbers-hint">{t("settings.cloudflare.providers.field.numbersHint")}</p>
        ) : null}
        {problem ? <Alert className="mt-3 mb-0" data-testid="provider-problem">{problem}</Alert> : null}
        {verdict ? (
          <div className="mt-3 flex flex-col gap-2 rounded-sm border border-line bg-surface-variant/50 px-3.5 py-3 text-[13px]" data-testid="provider-verdict">
            <p className={fresh ? "text-ok" : "text-muted"} role="status">
              {fresh
                ? verdict.result.settings_checked
                  ? t("settings.cloudflare.providers.verdict.ok")
                  : t("settings.cloudflare.providers.verdict.notBooted")
                : t("settings.cloudflare.providers.stale")}
            </p>
            <p className="text-muted">{t("settings.cloudflare.providers.verdict.variables")}</p>
            <ul className="flex flex-col gap-1">
              {verdict.result.variables.map((variable) => (
                <li key={variable.name} className="flex flex-wrap items-center gap-2" data-testid="verdict-variable">
                  <code dir="ltr" className="text-[12px] break-all">{variable.name}</code>
                  <Badge tone={variable.deleted ? "bad" : variable.added ? "ok" : "neutral"}>
                    {variable.deleted
                      ? t("settings.cloudflare.providers.verdict.deleted")
                      : variable.added
                        ? t("settings.cloudflare.providers.verdict.added")
                        : t("settings.cloudflare.providers.verdict.changed")}
                  </Badge>
                  {variable.secret ? <Badge tone="warn">{t("settings.cloudflare.providers.verdict.secret")}</Badge> : null}
                </li>
              ))}
            </ul>
            {verdict.result.creates_key ? <p>{t("settings.cloudflare.providers.verdict.createsKey")}</p> : null}
            {verdict.result.enables_consent ? (
              <p>
                <code dir="ltr" className="text-[12px]">AURA_ALLOW_HOSTED_INFERENCE</code>{" "}
                {t("settings.cloudflare.providers.verdict.consent")}
              </p>
            ) : null}
          </div>
        ) : null}
        {!verdict && !problem ? <p className="mt-3 text-[12.5px] text-muted">{t("settings.cloudflare.providers.checkFirst")}</p> : null}

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button disabled={body === null || name === "" || checking} onClick={() => void check()}>
            {checking ? <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" /> : null}
            {checking ? t("settings.cloudflare.providers.checking") : t("settings.cloudflare.providers.check")}
          </Button>
          <ConfirmDialog
            title={
              adding
                ? t("settings.cloudflare.providers.confirmAddTitle", { name })
                : t("settings.cloudflare.providers.confirmReplaceTitle", { name })
            }
            confirmText={name}
            disabled={!ready}
            onConfirm={apply}
          >
            {adding ? t("settings.cloudflare.providers.applyAdd") : t("settings.cloudflare.providers.applyReplace")}
          </ConfirmDialog>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
