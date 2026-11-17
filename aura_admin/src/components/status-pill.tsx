import { Badge } from "@/components/ui/badge";

/** Healthy pulses; anything else holds still so the eye goes to it. */
export function StatusPill({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <Badge tone={ok ? "ok" : "bad"} dot pulse={ok}>
      {label ?? (ok ? "healthy" : "down")}
    </Badge>
  );
}
