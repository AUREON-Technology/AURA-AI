"use client";

import { useState, type ReactNode } from "react";

export function ConfirmDialog({
  title,
  confirmText,
  onConfirm,
  children,
  danger = false,
}: {
  title: string;
  confirmText: string;
  onConfirm: () => Promise<void> | void;
  children: ReactNode;
  danger?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (typed !== confirmText) {
      setError(`Type ${confirmText} to confirm. This writes an audit event.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      setOpen(false);
      setTyped("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className={`btn ${danger ? "danger" : ""}`} type="button" onClick={() => setOpen(true)}>
        {children}
      </button>
      {open ? (
        <div className="card" style={{ marginTop: 12 }}>
          <strong>{title}</strong>
          <p className="muted">This writes an audit event.</p>
          <label className="field">
            Type {confirmText}
            <input value={typed} onChange={(e) => setTyped(e.target.value)} />
          </label>
          {error ? <p className="banner">{error}</p> : null}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" type="button" disabled={busy} onClick={run}>
              Confirm
            </button>
            <button className="btn" type="button" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}
