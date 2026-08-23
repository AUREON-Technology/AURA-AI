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

async function parseError(response: Response): Promise<AuraApiError> {
  const body = await response.json().catch(() => ({}));
  const detail = body.detail ?? body;
  if (typeof detail === "string") {
    return new AuraApiError(response.status, "request_failed", detail, false);
  }
  return new AuraApiError(
    response.status,
    detail.error || "request_failed",
    detail.message || detail.detail || `Request failed (${response.status})`,
    Boolean(detail.retryable),
  );
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
