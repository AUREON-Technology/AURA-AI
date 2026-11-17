"use client";

import { useState, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n/context";

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
  danger = false,
  disabled = false,
}: {
  title: string;
  confirmText: string;
  onConfirm: () => Promise<void> | void;
  children: ReactNode;
  danger?: boolean;
  disabled?: boolean;
}) {
  const { t, errorText } = useI18n();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [mismatch, setMismatch] = useState(false);
  const [error, setError] = useState<unknown>(null);

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

  return (
    <>
      <button
        className={`btn ${danger ? "danger" : ""}`}
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {children}
      </button>
      {open ? (
        <div className="card" style={{ marginTop: 12 }}>
          <strong>{title}</strong>
          <p className="muted">{t("confirm.auditNote")}</p>
          <label className="field">
            {t("confirm.typeLabel", { text: confirmText })}
            <input value={typed} onChange={(e) => setTyped(e.target.value)} />
          </label>
          {mismatch ? <p className="banner">{t("confirm.mismatch", { text: confirmText })}</p> : null}
          {error ? <p className="banner">{errorText(error)}</p> : null}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" type="button" disabled={busy} onClick={run}>
              {t("common.confirm")}
            </button>
            <button className="btn" type="button" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}
