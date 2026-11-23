import { AuraApiError } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

/** The gateway's `details.reason` values (control/cloudflare.py) that have a sentence of their own. */
const REASONS = new Set([
  "not_open_weight",
  "not_chat",
  "hosted_inference_not_consented",
  "provider_exists",
  "provider_name_collision",
  "railway_not_connected",
  "cloudflare_not_connected",
  "no_account",
  "bad_key_mode",
  "value_required",
  "last_provider",
  "not_a_cloudflare_provider",
  "name_mismatch",
  "change_in_progress",
  "confirmation_required",
]);

/** The dictionary key for a refusal reason, or null when there is none (then the gateway's own message is shown). */
export function refusalKey(reason: unknown): MessageKey | null {
  return typeof reason === "string" && REASONS.has(reason)
    ? (`settings.cloudflare.refusal.${reason}` as MessageKey)
    : null;
}

/**
 * A refusal or failure as text a person can act on: a known reason in words, anything else as the
 * gateway wrote it. Never includes a request body, so a pasted key cannot come back through here.
 */
export function problemText(
  err: unknown,
  t: (key: MessageKey) => string,
  errorText: (err: unknown) => string,
): string {
  if (err instanceof AuraApiError) {
    const key = refusalKey(err.details.reason);
    if (key) return t(key);
  }
  return errorText(err);
}
