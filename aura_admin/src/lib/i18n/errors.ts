import { AuraApiError } from "@/lib/api";

import type { MessageKey } from "./en";
import { translate, type Lang } from "./index";

// The gateway's `error` kinds (aura_core/domain/errors.py) that get dictionary text. Anything
// else, including the "request_failed" fallback for a plain-string `detail`, shows the raw message.
const KIND_KEYS: Record<string, MessageKey> = {
  unauthenticated: "errors.unauthenticated",
  authentication_error: "errors.unauthenticated",
  authorization_error: "errors.forbidden",
  not_found: "errors.notFound",
  conflict: "errors.conflict",
  validation_error: "errors.invalid",
  rate_limited: "errors.rateLimited",
  approval_required: "errors.approvalRequired",
  confirmation_invalid: "errors.confirmationInvalid",
  timeout: "errors.timeout",
  dependency_unavailable: "errors.unavailable",
  overloaded: "errors.overloaded",
  internal_error: "errors.internal",
  configuration_error: "errors.configuration",
  // Console-side refusals (app/api/auth/login/route.ts, src/lib/same-origin.ts).
  console_role_required: "login.roleRequired",
  cross_origin_refused: "errors.crossOrigin",
};

/**
 * Text for an error shown in a `.banner`. English keeps the gateway's own message, which is
 * English and more specific than any generic line. Arabic maps a known `error` kind to its
 * translation and keeps the gateway's message after it in parentheses, so no detail is lost;
 * an unknown kind falls back to the raw message.
 */
export function describeError(
  err: unknown,
  lang: Lang,
  t: (key: MessageKey) => string,
): string {
  const raw = err instanceof Error ? err.message : typeof err === "string" ? err : "";
  if (lang !== "en" && err instanceof AuraApiError && err.error in KIND_KEYS) {
    const key = KIND_KEYS[err.error];
    const mapped = t(key);
    // The gateway's text adds nothing when it is just the English of the same line.
    return raw && raw !== mapped && raw !== translate("en", key) ? `${mapped} (${raw})` : mapped;
  }
  return raw || t("errors.requestFailed");
}
