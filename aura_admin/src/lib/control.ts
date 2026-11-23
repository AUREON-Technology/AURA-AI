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
  // No status at all: a cron job between runs, or a service never deployed. Not a warning.
  if (!status) return "neutral";
  switch (status.toUpperCase()) {
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

// -- GPU Space (stage 3) ------------------------------------------------------------------------

export type SpaceOverview = {
  space_id: string;
  name: string;
  stage: string;
  hardware: string | null;
  requested_hardware: string | null;
  sleep_time: number | null;
  hardware_options: string[];
  sleep_choices: number[];
  change_in_progress: boolean;
  watch_minutes: number;
};

export type SpaceVariable = { name: string; secret: boolean; value: string | null };
export type SpaceSecret = { name: string; updated_at: string | null };

/** The Hub's stage words, as a badge tone. */
export function spaceTone(stage: string | null): "ok" | "warn" | "bad" | "neutral" {
  const word = (stage || "").toUpperCase();
  if (word === "RUNNING") return "ok";
  if (word.endsWith("_ERROR") || word === "NO_APP_FILE" || word === "DELETING") return "bad";
  if (word === "PAUSED" || word === "STOPPED") return "neutral";
  return "warn";
}

// -- Provider keys (stage 4) --------------------------------------------------------------------

export type KeySlotRow = {
  name: string;
  kind: string;
  host: string;
  api_key_env: string;
  /** null while Railway is not connected: the gateway's variables cannot be read. */
  key_set: boolean | null;
  /** both_sides: the GPU Space; create: a Workers AI slot; replace: paste a new value. */
  rotation: "replace" | "both_sides" | "create";
  healthy: boolean | null;
  state: string | null;
  cloudflare_token_id: string | null;
};

export type KeysOverview = {
  gateway_ready: boolean;
  space_id: string | null;
  cloudflare_ready: boolean;
  slots: KeySlotRow[];
};

export type CloudflareTokenRow = {
  token_id: string;
  name: string;
  status: string;
  issued_on: string | null;
  in_use: boolean;
};

// -- The Cloudflare tab (stage 6) ---------------------------------------------------------------

/** A Cloudflare provider as the gateway's Railway variables describe it (never its key). */
export type CloudflareProviderRow = {
  name: string;
  model: string;
  account_id: string;
  enabled: boolean;
  key_env: string;
  key_set: boolean;
  /** passive: the circuit breaker only; Cloudflare's /models path answers 405. */
  health_probe: string;
  quality_tier: number | null;
  context_window: number | null;
  max_output_tokens: number | null;
  rpm: number;
  rpd: number;
  tpm: number;
  tpd: number;
  tokens_per_second: number;
  json_mode: boolean;
  vision: boolean;
  healthy: boolean | null;
  state: string | null;
};

export type CloudflareOverview = {
  connected: boolean;
  /** Empty when Cloudflare was connected without one: the tab cannot run until it is replaced. */
  account_id: string;
  api_root: string;
  gateway_ready: boolean;
  /** null while Railway is not connected: the gateway's variables cannot be read. */
  hosted_inference_allowed: boolean | null;
  providers: CloudflareProviderRow[];
};

// -- Grafana Cloud and Qdrant Cloud (stage 5) -------------------------------------------------

export type MonitoringOverview = {
  stack: { slug: string; grafana_url: string };
  rules: { namespace: string; groups: { name: string; rules: number; loaded: boolean }[]; all_loaded: boolean };
  scrape: { state: "up" | "down" | "missing"; job: string; url: string; interval_s: number; auth: string; metrics_token_set: boolean };
  uptime: {
    checks: { name: string; url: string; frequency_s: number; success: string }[];
    observed: { job: string; instance: string; success_ratio: number | null }[];
  };
  firing: { alertname: string; severity: string }[];
  /** Status reads that could not run (a token without metrics:read): never read as all clear. */
  errors: string[];
};

export type ApplyResult = ChangeRow & { results: { group: string; ok: boolean; error: string }[] };

export type VectorOverview = {
  cluster: string;
  healthy: boolean;
  collections: {
    name: string;
    status: string;
    points_count: number | null;
    indexed_vectors_count: number | null;
    segments_count: number | null;
    vector_size: number | null;
    distance: string;
  }[];
};

export type VectorSnapshotRow = { name: string; created_at: string | null; size: number | null };

// -- The Cloudflare tab: the model catalog and provider changes (C2) -----------------------------

/** One Workers AI model from the live catalog, with what AURA will admit (control/cloudflare.py). */
export type CloudflareCatalogRow = {
  model: string;
  description: string;
  task: string;
  context_window: number | null;
  vision: boolean;
  function_calling: boolean;
  reasoning: boolean;
  /** Needs a paid Workers plan: the free daily allowance does not cover it. */
  paid_only: boolean;
  /** Cloudflare's own list of {unit, price, currency}, kept as it came: for display only. */
  price: { unit?: string; price?: number | string; currency?: string }[];
  chat: boolean;
  open_weight: boolean;
  can_add: boolean;
  /** "" when AURA can use the model, else why not. */
  refusal: "" | "not_chat" | "not_open_weight";
};

export type CloudflareCatalog = {
  account_id: string;
  models: CloudflareCatalogRow[];
  page: number;
  per_page: number;
  total: number;
};

export type CloudflareKeyMode = "create" | "paste" | "keep";

/** The body of Add and Replace. Strict on the gateway: numbers are numbers, never strings. */
export type CloudflareProviderBody = {
  model: string;
  context_window: number;
  quality_tier: number;
  max_output_tokens: number;
  enabled: boolean;
  rpm: number;
  rpd: number;
  tpm: number;
  tpd: number;
  tokens_per_second: number;
  json_mode: boolean;
  vision: boolean;
  key: CloudflareKeyMode;
  key_value?: string;
  enable_consent: boolean;
};

/** A dry run of a provider change: the variable names it would write (never a value) and the verdict. */
export type CloudflareValidateResult = {
  ok: boolean;
  settings_checked: boolean;
  variables: { name: string; secret: boolean; deleted: boolean; added: boolean }[];
  creates_key?: boolean;
  enables_consent?: boolean;
  revoke_token_id?: string | null;
};

// -- Cloudflare usage and R2 (stage 6, C3) ------------------------------------------------------
// Read-only views. Every Cloudflare-side outcome is a `state` in a 200, never an error.

export type CloudflareUsage = {
  state: "ok" | "no_permission" | "unavailable";
  account_id: string;
  /** The UTC day the figures are for (YYYY-MM-DD) and when the allowance resets (ISO, 00:00 UTC). */
  day: string;
  resets_at: string;
  requests: number | null;
  /** null when Cloudflare names no neuron field (neurons_available is false). */
  neurons: number | null;
  neurons_available: boolean;
  /** The GraphQL field the neurons came from: empty when there are none. */
  neurons_field: string;
  /** A short code for why it is unavailable; never Cloudflare's own text. */
  reason: string;
  /** The free-plan allowance: the plan tier is not known to the console. */
  allowance_neurons: number;
  allowance_basis: "free_plan";
  /** The token permission the view needs, named when it is missing. */
  permission: string;
};

export type CloudflareBucketRow = {
  name: string;
  created: string;
  location: string;
  storage_class: string;
  /** The number of lock rules, or null when Cloudflare would not say. */
  lock_rules: number | null;
};

export type CloudflareR2 = {
  state: "ok" | "not_enabled" | "no_permission" | "unavailable";
  account_id: string;
  reason: string;
  permission: string;
  /** More buckets exist than this list shows. */
  more: boolean;
  buckets: CloudflareBucketRow[];
};
