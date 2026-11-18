"use client";

import { useId, useState, type ReactNode } from "react";
import { LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n/context";
import { cn } from "@/lib/utils";

/**
 * Fold the Arabic letter variants a keyboard makes easy to mix up (أ إ آ → ا, ى → ي), so a typed
 * Arabic confirmation word matches whichever spelling the dictionary uses. Latin text is untouched.
 */
function foldConfirm(value: string): string {
  return value.replace(/[أإآ]/g, "ا").replace(/ى/g, "ي");
}

export function ConfirmDialog({
  title,
  confirmText,
  onConfirm,
  children,
  body,
  label,
  danger = false,
  disabled = false,
  size = "sm",
}: {
  title: string;
  confirmText: string;
  onConfirm: () => Promise<void> | void;
  children: ReactNode;
  /** Extra fields shown above the confirmation word, e.g. a value the action takes. */
  body?: ReactNode;
  /** Accessible name for the trigger when its visible text repeats on every row. */
  label?: string;
  danger?: boolean;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  const { t, errorText } = useI18n();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [mismatch, setMismatch] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const inputId = useId();

  // A reopened dialog starts clean: no earlier refusal or half-typed word.
  function close() {
    setOpen(false);
    setTyped("");
    setMismatch(false);
    setError(null);
  }

  async function run() {
    if (foldConfirm(typed) !== foldConfirm(confirmText)) {
      setMismatch(true);
      setError(null);
      return;
    }
    setBusy(true);
    setMismatch(false);
    setError(null);
    try {
      await onConfirm();
      setOpen(false);
      setTyped("");
    } catch (err) {
      setError(err ?? new Error(t("errors.requestFailed")));
    } finally {
      setBusy(false);
    }
  }

  const Icon = danger ? TriangleAlert : ShieldCheck;
  // "Type {text}" with the word itself set as code, in whichever order the language puts it.
  const [labelBefore, labelAfter = ""] = t("confirm.typeLabel", { text: "\u0000" }).split("\u0000");

  return (
    <>
      <Button
        variant={danger ? "danger" : "default"}
        size={size}
        disabled={disabled}
        aria-label={label}
        onClick={() => setOpen(true)}
      >
        {children}
      </Button>
      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void run();
            }}
          >
            <div className="flex items-start gap-3.5">
              <span
                aria-hidden="true"
                className={cn(
                  "grid size-10 shrink-0 place-items-center rounded-full",
                  danger ? "bg-bad-soft text-bad" : "bg-brand-soft text-accent",
                )}
              >
                <Icon className="size-5" />
              </span>
              <div className="min-w-0 pt-0.5">
                <DialogTitle className="break-words">{title}</DialogTitle>
                <DialogDescription>{t("confirm.auditNote")}</DialogDescription>
              </div>
            </div>
            {body ? <div className="mt-5">{body}</div> : null}
            <div className="mt-5 flex flex-col gap-1.5">
              <label htmlFor={inputId} className="text-[12.5px] font-medium text-muted">
                {labelBefore}
                <code className="rounded-[5px] bg-surface-variant px-1.5 py-0.5 text-[12px] text-fg">{confirmText}</code>
                {labelAfter}
              </label>
              <Input
                id={inputId}
                autoComplete="off"
                spellCheck={false}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
            {mismatch ? <Alert className="mt-3 mb-0">{t("confirm.mismatch", { text: confirmText })}</Alert> : null}
            {error ? <Alert className="mt-3 mb-0">{errorText(error)}</Alert> : null}
            <DialogFooter>
              <Button variant="ghost" onClick={close}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" variant={danger ? "danger" : "primary"} disabled={busy}>
                {busy ? <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" /> : null}
                {t("common.confirm")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
