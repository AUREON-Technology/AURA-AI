export function StatusPill({ ok, label }: { ok: boolean; label?: string }) {
  return <span className={`pill ${ok ? "ok" : "bad"}`}>{label ?? (ok ? "healthy" : "down")}</span>;
}
