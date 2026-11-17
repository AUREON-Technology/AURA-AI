import type { Me } from "./types";

export class AuraApiError extends Error {
  constructor(
    public status: number,
    public error: string,
    message: string,
    public retryable = false,
  ) {
    super(message);
  }
}

function basePath(): string {
  return process.env.NEXT_PUBLIC_BASE_PATH || "";
}

const VALIDATION_SOURCES = new Set(["body", "query", "path", "header", "cookie"]);

function describeValidation(items: unknown[]): string {
  return items
    .map((item) => {
      const { loc, msg } = (item ?? {}) as { loc?: unknown; msg?: unknown };
      // FastAPI prefixes the source ("body", "query", ...); the field is what an operator can
      // act on.
      const parts = Array.isArray(loc) ? [...loc] : [];
      if (typeof parts[0] === "string" && VALIDATION_SOURCES.has(parts[0])) {
        parts.shift();
      }
      const where = parts.join(".");
      const text = typeof msg === "string" ? msg : JSON.stringify(item);
      return where ? `${where}: ${text}` : text;
    })
    .join("; ");
}

// The gateway answers errors in four shapes (audit INT-029):
//   - an AuraError envelope: {error, message, retryable, details} at the top level and again
//     under `detail` (aura_core routes);
//   - a plain string `detail` (HTTPException from the platform routes);
//   - FastAPI's 422: `detail` is a list of {loc, msg, type};
//   - a dict `detail` with no message at all (e.g. {"allowed": [...]} on a role PATCH).
// Each becomes a message an operator can read; none collapses to a bare status.
export function errorFromBody(status: number, body: unknown): AuraApiError {
  const fallback = `Request failed (${status})`;
  if (typeof body === "string") {
    return new AuraApiError(status, "request_failed", body.trim() || fallback);
  }
  if (!body || typeof body !== "object") {
    return new AuraApiError(status, "request_failed", fallback);
  }
  const record = body as Record<string, unknown>;
  const detail = record.detail ?? record;
  if (typeof detail === "string") {
    return new AuraApiError(status, "request_failed", detail || fallback);
  }
  if (Array.isArray(detail)) {
    return new AuraApiError(status, "validation_error", describeValidation(detail) || fallback);
  }
  if (detail && typeof detail === "object") {
    const fields = detail as Record<string, unknown>;
    const message =
      (typeof fields.message === "string" && fields.message) ||
      (typeof fields.detail === "string" && fields.detail) ||
      (Object.keys(fields).length ? `${fallback}: ${JSON.stringify(fields).slice(0, 300)}` : fallback);
    return new AuraApiError(
      status,
      typeof fields.error === "string" ? fields.error : "request_failed",
      message,
      Boolean(fields.retryable),
    );
  }
  return new AuraApiError(status, "request_failed", fallback);
}

async function parseError(response: Response): Promise<AuraApiError> {
  const body: unknown = await response.json().catch(() => null);
  return errorFromBody(response.status, body);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("x-correlation-id", crypto.randomUUID());
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(`${basePath()}/api/v3${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 401) {
    window.location.assign(`${basePath()}/login`);
    throw new AuraApiError(401, "unauthenticated", "Session expired");
  }
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function fetchMe(): Promise<Me> {
  return api<Me>("/admin/me");
}
