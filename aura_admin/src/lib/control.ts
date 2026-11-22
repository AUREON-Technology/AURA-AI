// The Super-Admin control center's client side (ADR-037): types, the step-up re-sign-in, and the
// write helper every control-center change goes through.
//
// A write needs a step-up grant, bought with a *fresh* Google sign-in: the gateway issues a
// one-time challenge, Google signs the person in again (we ask prompt=login, max_age) in a popup, and
// the gateway trades the ID token for a five-minute grant. The grant lives in memory only -- never
// localStorage or a cookie -- so closing the tab drops it, and a stolen session cookie cannot buy
// one.
import { AuraApiError, api } from "@/lib/api";

export type ControlPlatform = {
  platform: "railway" | "hf_space" | "grafana" | "qdrant" | "cloudflare";
  connected: boolean;
  verified: boolean;
  verified_at: string | null;
  verified_detail: string;
  target: string;
  configured_by: string | null;
  configured_at: string | null;
  token_hint: string;
};

export type ControlOverview = {
  platforms: ControlPlatform[];
  open_changes: number;
  vault_ready: boolean;
  environment: string;
  release: Record<string, unknown>;
};

export type CatalogEntry = {
  section: string;
  field: string;
  variable: string;
  type: string;
  default: unknown;
  description: string;
  secret: boolean;
  protected: boolean;
  set: boolean;
  value: unknown;
  minimum: number | null;
  maximum: number | null;
  requires_redeploy: boolean;
  /** The variable as Railway holds it (may differ from value until the next redeploy). */
  raw?: string | null;
};

export type CatalogGroup = { section: string; settings: CatalogEntry[] };

export type ChangeRow = {
  change_id: string;
  actor: string;
  platform: string;
  target: string;
  action: string;
  status: "recorded" | "watching" | "succeeded" | "rolling_back" | "rolled_back" | "failed";
  detail: Record<string, unknown>;
  deployment_id: string | null;
  previous_deployment_id: string | null;
  reason: string;
  created_at: string;
  updated_at: string;
};

export const STEP_UP_HEADER = "x-aura-step-up";

/** Whether a refusal is "do the fresh sign-in again" rather than a real error. */
export function needsStepUp(err: unknown): boolean {
  return err instanceof AuraApiError && err.status === 403 && err.details.reason === "step_up_required";
}

type Grant = { value: string; expiresAt: number };

/** Opens Google in a popup for a fresh sign-in and resolves with the ID token it returns. */
function googleReSignIn(nonce: string, maxAgeS: number, email: string): Promise<string> {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
  if (!clientId) {
    return Promise.reject(new Error("NEXT_PUBLIC_GOOGLE_CLIENT_ID is not set"));
  }
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
  const redirect = `${window.location.origin}${base}/step-up/callback`;
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "id_token",
    scope: "openid email",
    redirect_uri: redirect,
    nonce,
    prompt: "login",
    max_age: String(maxAgeS),
    login_hint: email,
  });
  const popup = window.open(
    `https://accounts.google.com/o/oauth2/v2/auth?${params}`,
    "aura-step-up",
    "popup,width=480,height=640",
  );
  if (!popup) {
    return Promise.reject(new Error("popup_blocked"));
  }
  return new Promise((resolve, reject) => {
    const timer = window.setInterval(() => {
      if (popup.closed) {
        cleanup();
        reject(new Error("step_up_cancelled"));
      }
    }, 500);
    function onMessage(event: MessageEvent) {
      // Only our own callback page, on our own origin, may answer.
      if (event.origin !== window.location.origin || event.source !== popup) {
        return;
      }
      const data = event.data as { type?: string; idToken?: string; error?: string } | null;
      if (data?.type !== "aura-step-up") {
        return;
      }
      cleanup();
      popup?.close();
      if (data.idToken) {
        resolve(data.idToken);
      } else {
        reject(new Error(data.error || "step_up_failed"));
      }
    }
    function cleanup() {
      window.clearInterval(timer);
      window.removeEventListener("message", onMessage);
    }
    window.addEventListener("message", onMessage);
  });
}

/** Holds the grant for this tab and buys a new one when it is missing or expired. */
export class StepUp {
  private grant: Grant | null = null;
  private pending: Promise<string> | null = null;

  constructor(private readonly email: string) {}

  clear(): void {
    this.grant = null;
  }

  get active(): boolean {
    return this.grant !== null && this.grant.expiresAt - Date.now() > 15_000;
  }

  async ensure(): Promise<string> {
    if (this.active && this.grant) {
      return this.grant.value;
    }
    // Two writes racing for a grant share one sign-in.
    this.pending ??= this.acquire().finally(() => {
      this.pending = null;
    });
    return this.pending;
  }

  private async acquire(): Promise<string> {
    const challenge = await api<{ nonce: string; max_age_s: number }>("/admin/control/step-up/challenge", {
      method: "POST",
    });
    const idToken =
      process.env.NEXT_PUBLIC_DEV_LOGIN === "1"
        ? `mock_step_up:${challenge.nonce}`
        : await googleReSignIn(challenge.nonce, challenge.max_age_s, this.email);
    const redeemed = await api<{ grant: string; expires_at: string }>("/admin/control/step-up", {
      method: "POST",
      body: JSON.stringify({ id_token: idToken, nonce: challenge.nonce }),
    });
    this.grant = { value: redeemed.grant, expiresAt: Date.parse(redeemed.expires_at) };
    return redeemed.grant;
  }

  /** A control-center write: with a grant, and once more after a fresh sign-in if it expired. */
  async write<T>(path: string, body: unknown): Promise<T> {
    const send = async (grant: string) =>
      api<T>(path, {
        method: "POST",
        headers: { [STEP_UP_HEADER]: grant },
        body: JSON.stringify(body),
      });
    try {
      return await send(await this.ensure());
    } catch (err) {
      if (!needsStepUp(err)) {
        throw err;
      }
      this.clear();
      return send(await this.ensure());
    }
  }
}

// -- Railway (stage 2) --------------------------------------------------------------------------

export type RailwayServiceRow = {
  service_id: string;
  name: string;
  latest_deployment_id: string | null;
  latest_status: string | null;
  latest_at: string | null;
  healthcheck_path: string;
  is_gateway: boolean;
  change_in_progress: boolean;
};

export type RailwayOverview = {
  project: { id: string; name: string };
  environment: { id: string; name: string };
  services: RailwayServiceRow[];
};

export type RailwayVariable = {
  name: string;
  secret: boolean;
  /** Never present for a secret: the gateway does not send it. */
  value: string | null;
  reference: boolean;
  protected: boolean;
  provided: boolean;
};

export type RailwayDeployment = {
  deployment_id: string;
  status: string;
  created_at: string | null;
  can_rollback: boolean;
  can_redeploy: boolean;
  commit: string;
  message: string;
};

export type LogRow = { timestamp: string; severity: string; message: string };

/** name -> new value, or null to delete. Values of secret names are never shown back. */
export type PendingChanges = Record<string, string | null>;

const SECRET_NAME = /(KEY|KEYS|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE|DSN)$/;
const URL_USERINFO = /^[a-z][a-z0-9+.-]*:\/\/[^/@\s]+@/i;

/** The gateway's own rule (aura_core/domain/control.py), so the tray never echoes a secret. */
export function isSecretVariable(name: string, value?: string | null): boolean {
  const upper = name.trim().toUpperCase();
  if (upper.endsWith("_ENV")) return Boolean(value && URL_USERINFO.test(value.trim()));
  return SECRET_NAME.test(upper) || Boolean(value && URL_USERINFO.test(value.trim()));
}

/** Railway's status words, as a badge tone. */
export function deploymentTone(status: string | null): "ok" | "warn" | "bad" | "neutral" {
  switch ((status || "").toUpperCase()) {
    case "SUCCESS":
    case "SLEEPING":
      return "ok";
    case "FAILED":
    case "CRASHED":
      return "bad";
    case "REMOVED":
    case "REMOVING":
    case "SKIPPED":
      return "neutral";
    default:
      return "warn";
  }
}
