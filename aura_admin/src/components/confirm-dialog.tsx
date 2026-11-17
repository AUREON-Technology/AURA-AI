"use client";

import { useId, useState, type ReactNode } from "react";
import { LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function ConfirmDialog({
  title,
  confirmText,
  onConfirm,
  children,
  danger = false,
  disabled = false,
  size = "sm",
}: {
  title: string;
  confirmText: string;
  onConfirm: () => Promise<void> | void;
  children: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();

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

  const Icon = danger ? TriangleAlert : ShieldCheck;

  return (
    <>
      <Button variant={danger ? "danger" : "default"} size={size} disabled={disabled} onClick={() => setOpen(true)}>
        {children}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
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
                <DialogDescription>This writes an audit event.</DialogDescription>
              </div>
            </div>
            <div className="mt-5 flex flex-col gap-1.5">
              <label htmlFor={inputId} className="text-[12.5px] font-medium text-muted">
                Type <code className="rounded-[5px] bg-surface-variant px-1.5 py-0.5 text-[12px] text-fg">{confirmText}</code>
              </label>
              <Input
                id={inputId}
                autoComplete="off"
                spellCheck={false}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
            {error ? <Alert className="mt-3 mb-0">{error}</Alert> : null}
            <DialogFooter>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" variant={danger ? "danger" : "primary"} disabled={busy}>
                {busy ? <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" /> : null}
                Confirm
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
