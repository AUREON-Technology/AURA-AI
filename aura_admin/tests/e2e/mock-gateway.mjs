import http from "node:http";

const PORT = Number(process.env.MOCK_GATEWAY_PORT || 8010);

// Stands in for the real gateway in e2e. It enforces what the real handlers enforce (audit
// INT-035), so a console change that only works against a permissive mock fails here too:
//   - roles: aura_core/api/admin.py (require_admin / require_super_admin) and
//     api/admin_platform.py (require_role), including SuperAdmin-only approval kinds (INT-006);
//   - error shapes: an AuraError-style envelope from aura_core routes ({error, message,
//     retryable, details} at the top level and again under `detail`), a plain string `detail`
//     from the platform routes, FastAPI's list `detail` for a 422, and {"detail": "Not Found"}
//     for an unknown route;
//   - /admin/me returns exactly what api/admin_platform.py's whoami returns (no tokens).

const principals = {
  mock_token: {
    uid: "a1",
    email: "ops@aura.ai",
    name: "Ops",
    role: "Admin",
    token: "admin-access",
  },
  mock_token_super: {
    uid: "s1",
    email: "root@aura.ai",
    name: "Root",
    role: "SuperAdmin",
    token: "super-access",
  },
  mock_token_dev: {
    uid: "d1",
    email: "dev@aura.ai",
    name: "Dev",
    role: "Developer",
    token: "dev-access",
  },
};

const RANK = { User: 0, Developer: 3, Admin: 4, SuperAdmin: 5 };

const tokens = {
  "admin-access": principals.mock_token,
  "super-access": principals.mock_token_super,
  "dev-access": principals.mock_token_dev,
};

// Refresh tokens are single use, as on the real gateway (jwt_auth._refresh_store_take): the
// first /auth/refresh deletes the token, and any replay of it is refused with 401.
const refreshTokens = {};
const refreshReplays = {};
const revokedRefresh = [];
// Refresh tokens whose refresh answers with an outage status instead (INT-104): the token is
// neither spent nor refused, as when the gateway fails before it reaches the refresh store.
const refreshOutages = {};
let issued = 0;

function issuePair(principal) {
  issued += 1;
  const access = `${principal.token}-${issued}`;
  const refresh = `${principal.token}-refresh-${issued}`;
  tokens[access] = principal;
  refreshTokens[refresh] = principal;
  return { token_type: "bearer", access_token: access, refresh_token: refresh, expires_in: 900 };
}

// SUPERADMIN_EMAILS / ADMIN_EMAILS stand-ins: a role an env list grants is a floor the Access
// page cannot lower (api/admin_platform.py assign_role, 409).
const ENV_GRANTS = { "root@aura.ai": ["SuperAdmin", "SUPERADMIN_EMAILS"] };

const CONTROL_PLATFORMS = ["railway", "hf_space", "grafana", "qdrant", "cloudflare"];

// A slice of what /admin/control/settings returns (aura_core/control/catalog.py): one plain
// setting, one with bounds, a protected one and two secrets (no value is ever present).
const CONTROL_CATALOG = [
  {
    section: "knowledge",
    settings: [
      { section: "knowledge", field: "retrieval_top_k", variable: "AURA_KNOWLEDGE_TOP_K", type: "int", default: 2,
        description: "How many chunks a turn retrieves.", secret: false, protected: false, set: true, value: 4,
        minimum: 1, maximum: 50, requires_redeploy: true },
    ],
  },
  {
    section: "infrastructure",
    settings: [
      { section: "infrastructure", field: "database_url", variable: "AURA_DATABASE_URL", type: "str", default: "",
        description: "Postgres.", secret: true, protected: true, set: true, value: null, minimum: null, maximum: null,
        requires_redeploy: true },
    ],
  },
  {
    section: "email",
    settings: [
      { section: "email", field: "smtp_password", variable: "AURA_SMTP_PASSWORD", type: "secret", default: null,
        description: "", secret: true, protected: false, set: false, value: null, minimum: null, maximum: null,
        requires_redeploy: true },
    ],
  },
];

// Railway as the control center sees it (aura_core/control/railway.py + InMemoryRailway): the
// same refusals, and secret values that never leave the mock.
const RAILWAY_SECRET = "rw-secret-value-never-shown";
const SECRET_NAME = /(KEY|KEYS|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE|DSN)$/;
const PROTECTED = new Set(["AURA_SECRETS_ENCRYPTION_KEY", "AURA_DATA_ENCRYPTION_KEYS", "AURA_DATABASE_URL"]);

function freshRailway() {
  const at = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();
  return {
    seq: 3,
    services: {
      "svc-gw": {
        name: "gateway", healthcheck: "/health/live",
        vars: {
          AURA_ENVIRONMENT: "production", AURA_KNOWLEDGE_TOP_K: "4", AURA_JWT_SECRET: RAILWAY_SECRET,
          AURA_DATABASE_URL: "${{Postgres.DATABASE_URL}}", RAILWAY_SERVICE_NAME: "gateway",
        },
        deployments: [
          { deployment_id: "dep-2", status: "SUCCESS", created_at: at(30), can_rollback: true, can_redeploy: true, commit: "85258222c84c", message: "fix(control): accept Google re-sign-ins" },
          { deployment_id: "dep-1", status: "REMOVED", created_at: at(90), can_rollback: true, can_redeploy: true, commit: "30b2b251be7c", message: "control center stage 1" },
        ],
      },
      "svc-console": {
        name: "console", healthcheck: "",
        vars: { NEXT_PUBLIC_BASE_PATH: "/console" },
        deployments: [
          { deployment_id: "dep-3", status: "SUCCESS", created_at: at(20), can_rollback: true, can_redeploy: true, commit: "85258222c84c", message: "console" },
        ],
      },
    },
    logs: {
      "deploy:dep-2": [
        { timestamp: "2026-11-21T21:50:01Z", severity: "info", message: "uvicorn running" },
        { timestamp: "2026-11-21T21:50:02Z", severity: "info", message: `jwt loaded ${RAILWAY_SECRET}` },
      ],
      "build:dep-2": [{ timestamp: "2026-11-21T21:48:00Z", severity: "info", message: "image built" }],
    },
    restarts: [],
    applied: [],
  };
}

// The inference Space (aura_core/control/space.py + InMemorySpace).
const SPACE_SECRET = "hf-space-secret-never-shown";
const SPACE_HARDWARE = ["cpu-basic", "cpu-upgrade", "t4-small", "l4x1", "a10g-large", "a100-large"];
const SLEEP_CHOICES = [-1, 300, 900, 1800, 3600, 10800, 36000, 86400, 172800];

function freshSpace() {
  return {
    stage: "RUNNING", hardware: "l4x1", sleep_time: 3600,
    vars: { MODEL_ID: "Qwen/Qwen2.5-7B-Instruct-AWQ", MAX_MODEL_LEN: "8192" },
    secrets: { HF_TOKEN: { value: SPACE_SECRET, updated_at: "2026-11-20T10:00:00Z" } },
    events: [],
    logs: { run: ["INFO vLLM engine started", "INFO Uvicorn running on 0.0.0.0:7860"], build: ["Step 1/9 : FROM vllm"] },
  };
}

function isSecret(name, value) {
  return (!name.endsWith("_ENV") && SECRET_NAME.test(name)) || /^[a-z][a-z0-9+.-]*:\/\/[^/@\s]+@/i.test(value || "");
}

const state = {
  control: { platforms: {}, changes: [], nonces: {}, grants: {}, stepUps: 0, receivedTokens: [], vaultReady: true, railway: freshRailway(), space: freshSpace() },
  operators: [
    {
      email: "ops@aura.ai",
      auth_subject: "a1",
      display_name: "Ops",
      role: "Developer",
      granted_by: "Access page",
    },
    {
      email: "root@aura.ai",
      auth_subject: "s1",
      display_name: "Root",
      role: "SuperAdmin",
      granted_by: "SUPERADMIN_EMAILS",
    },
  ],
  approvals: [
    {
      id: "appr-1",
      kind: "model_promotion",
      summary: "Promote llama canary",
      status: "pending",
      artefact_digest: "abc",
      expires_at: null,
      decided_by: null,
    },
    {
      id: "appr-bp",
      kind: "blueprint_deployment",
      summary: "Deploy blueprint v3",
      status: "pending",
      artefact_digest: "bp-digest",
      expires_at: null,
      decided_by: null,
    },
  ],
  sources: [
    { id: "docs", type: "web", uri: "https://example.com/docs", enabled: true, trust_tier: 2, last_success_at: null },
    // PATCH on this one fails the way a store outage does, for the console's error banner.
    { id: "flaky", type: "web", uri: "https://example.com/flaky", enabled: true, trust_tier: 2, last_success_at: null },
  ],
  localModels: [{ name: "qwen2.5:3b" }, { name: "library/llama3:8b" }],
  // Raw request paths of DELETE /admin/local-models/..., to check the proxy kept the encoding.
  localModelDeletes: [],
  revoked: [],
  promptPin: null,
  // aura_core/billing/service.py BillingService.customers rows, in workspace_id order.
  customers: [
    {
      workspace_id: "ws-ali", workspace_name: "Ali", kind: "personal", plan: "pro", seats: 1,
      status: "past_due", provider: "fawry", period_end: "2026-11-10T00:00:00+00:00", grace_until: null,
      last_payment: { status: "failed", amount_piastres: 25000, at: "2026-11-10T00:05:00+00:00" },
    },
    {
      workspace_id: "ws-ios", workspace_name: "Mona", kind: "personal", plan: "pro", seats: 1,
      status: "grace", provider: "apple", period_end: "2026-11-12T00:00:00+00:00",
      grace_until: "2026-11-19T00:00:00+00:00", last_payment: null,
    },
    {
      workspace_id: "ws-refund", workspace_name: "Refunded Co", kind: "team", plan: "team", seats: 3,
      status: "past_due", provider: "fawry", period_end: "2026-11-11T00:00:00+00:00", grace_until: null,
      last_payment: { status: "refunded", amount_piastres: 90000, at: "2026-10-11T09:00:00+00:00" },
    },
    {
      workspace_id: "ws-team", workspace_name: "Nile Labs", kind: "team", plan: "team", seats: 5,
      status: "active", provider: "fawry", period_end: "2026-12-01T00:00:00+00:00", grace_until: null,
      last_payment: { status: "paid", amount_piastres: 150000, at: "2026-11-01T10:00:00+00:00" },
    },
  ],
  customerLists: 0,
  graceCalls: 0,
};

const CUSTOMERS_AT_START = JSON.stringify(state.customers);

const SUPER_ADMIN_APPROVAL_KINDS = new Set(["connector_registration", "blueprint_deployment"]);

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

// AuraError.to_dict() raised as an HTTPException detail: v3's handler puts it at the top level
// and keeps it under `detail` (aura_core/api/v3.py, wrapped_domain_error_handler).
function auraError(response, status, error, message, retryable = false, details = {}) {
  const body = { error, message, retryable, details };
  json(response, status, { ...body, detail: body });
}

function readBody(request) {
  return new Promise((resolve) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function actor(request) {
  const header = request.headers.authorization || "";
  const token = header.replace(/^Bearer\s+/i, "");
  return tokens[token] || null;
}

// aura_core/api/admin.py and v3.py: require_admin / require_super_admin.
function coreGate(response, who, minimum) {
  if (RANK[who.role] >= RANK[minimum]) {
    return true;
  }
  const message =
    minimum === "SuperAdmin"
      ? "this endpoint requires a SuperAdmin role"
      : "this endpoint requires an administrator role";
  auraError(response, 403, "authorization_error", message);
  return false;
}

// api/admin_platform.py: services.rbac.require_role.
function platformGate(response, who, minimum) {
  if (RANK[who.role] >= RANK[minimum]) {
    return true;
  }
  json(response, 403, { detail: `Access forbidden: requires at least ${minimum} role.` });
  return false;
}

function superAdminCount() {
  const emails = new Set(state.operators.filter((row) => row.role === "SuperAdmin").map((row) => row.email));
  for (const [email, [role]] of Object.entries(ENV_GRANTS)) {
    if (role === "SuperAdmin") {
      emails.add(email);
    }
  }
  return emails.size;
}

const server = http.createServer(async (request, response) => {
  const rawPath = (request.url || "/").split("?")[0];
  const url = new URL(request.url || "/", "http://127.0.0.1");
  // Starlette routes on the percent-decoded path, as this does.
  const path = decodeURIComponent(url.pathname);
  const method = request.method || "GET";
  const who = actor(request);

  if (method === "GET" && (path === "/" || path === "/healthz")) {
    json(response, 200, { ok: true });
    return;
  }

  if (method === "POST" && path === "/api/v3/auth/web/token") {
    const idToken = (request.headers.authorization || "").replace(/^Bearer\s+/i, "");
    const principal = principals[idToken];
    if (!principal) {
      json(response, 403, { detail: "operator_required" });
      return;
    }
    json(response, 200, issuePair(principal));
    return;
  }

  if (method === "POST" && path === "/api/v3/auth/refresh") {
    const body = await readBody(request);
    const outage = refreshOutages[body.refresh_token];
    if (outage) {
      json(response, outage, { detail: "Service temporarily unavailable" });
      return;
    }
    const principal = refreshTokens[body.refresh_token];
    if (!principal) {
      refreshReplays[body.refresh_token] = (refreshReplays[body.refresh_token] || 0) + 1;
      json(response, 401, { detail: "Invalid or expired refresh token" });
      return;
    }
    delete refreshTokens[body.refresh_token];
    json(response, 200, issuePair(principal));
    return;
  }

  if (method === "POST" && path === "/api/v3/auth/revoke") {
    const body = await readBody(request);
    delete refreshTokens[body.refresh_token];
    revokedRefresh.push(body.refresh_token);
    json(response, 200, { revoked: true });
    return;
  }

  // Test hooks: expire one access token (as if its 15 minutes ran out), report how many
  // times a spent refresh token was replayed, and what reached the local-model delete.
  if (method === "POST" && path === "/__test/expire-access") {
    const body = await readBody(request);
    delete tokens[body.access_token];
    json(response, 200, { expired: body.access_token });
    return;
  }

  // Makes one refresh token's refresh answer `status` (e.g. 503) until called with status null.
  if (method === "POST" && path === "/__test/refresh-outage") {
    const body = await readBody(request);
    if (body.status) {
      refreshOutages[body.refresh_token] = body.status;
    } else {
      delete refreshOutages[body.refresh_token];
    }
    json(response, 200, { refresh_token: body.refresh_token, status: body.status ?? null });
    return;
  }

  if (method === "GET" && path === "/__test/refresh-replays") {
    json(response, 200, { replays: Object.values(refreshReplays).reduce((a, b) => a + b, 0) });
    return;
  }

  if (method === "GET" && path === "/__test/local-model-deletes") {
    json(response, 200, { paths: state.localModelDeletes });
    return;
  }

  if (method === "GET" && path === "/__test/customer-lists") {
    json(response, 200, { calls: state.customerLists, grace_calls: state.graceCalls });
    return;
  }

  // Puts the customers back as they started, so customers.spec.ts holds against a reused mock.
  if (method === "GET" && path === "/__test/control") {
    json(response, 200, {
      step_ups: state.control.stepUps,
      tokens_received: state.control.receivedTokens,
      live_grants: Object.keys(state.control.grants).length,
      railway_vars: state.control.railway.services["svc-gw"].vars.AURA_KNOWLEDGE_TOP_K,
      railway_applied: state.control.railway.applied,
      railway_restarts: state.control.railway.restarts,
      space_events: state.control.space.events,
      space_vars: state.control.space.vars,
      space_secret_names: Object.keys(state.control.space.secrets),
    });
    return;
  }
  if (method === "POST" && path === "/__test/reset-control") {
    const vaultReady = !new URL(request.url, "http://mock").searchParams.has("no-vault");
    state.control = { platforms: {}, changes: [], nonces: {}, grants: {}, stepUps: 0, receivedTokens: [], vaultReady, railway: freshRailway(), space: freshSpace() };
    json(response, 200, { ok: true });
    return;
  }
  if (method === "POST" && path === "/__test/reset-customers") {
    state.customers = JSON.parse(CUSTOMERS_AT_START);
    json(response, 200, { reset: true });
    return;
  }

  if (method === "GET" && path === "/__test/revoked-refresh") {
    json(response, 200, { revoked: revokedRefresh });
    return;
  }

  if (!who) {
    auraError(response, 401, "authentication_error", "missing token");
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/me") {
    if (!platformGate(response, who, "Developer")) return;
    json(response, 200, {
      uid: who.uid,
      email: who.email,
      name: who.name,
      role: who.role,
      is_admin: RANK[who.role] >= RANK.Admin,
      is_super_admin: who.role === "SuperAdmin",
    });
    return;
  }

  // -- control center (aura_core/api/admin_control.py): SuperAdmin only; writes need a step-up
  // grant (X-Aura-Step-Up) bought with a one-time challenge, and the typed confirmation. Tokens
  // are write-only: nothing below ever returns one.
  if (path.startsWith("/api/v3/admin/control/")) {
    if (!coreGate(response, who, "SuperAdmin")) return;
    const control = state.control;
    const sub = who.uid;
    const now = Date.now();
    const requireGrant = () => {
      const grant = request.headers["x-aura-step-up"];
      const found = grant && control.grants[grant];
      if (!found || found.sub !== sub || found.expires < now) {
        auraError(response, 403, "authorization_error", "this change needs a fresh sign-in", false, {
          reason: "step_up_required",
        });
        return false;
      }
      return true;
    };
    const journal = (platformName, action) => {
      control.changes.unshift({
        change_id: `chg_${control.changes.length + 1}`, actor: who.email, platform: platformName,
        target: control.platforms[platformName]?.target || platformName, action, status: "recorded",
        detail: {}, deployment_id: null, previous_deployment_id: null, reason: "",
        created_at: new Date(now).toISOString(), updated_at: new Date(now).toISOString(),
      });
    };
    if (method === "GET" && path === "/api/v3/admin/control/overview") {
      json(response, 200, {
        platforms: CONTROL_PLATFORMS.map((name) => {
          const row = control.platforms[name];
          return {
            platform: name, connected: Boolean(row), verified: Boolean(row?.verified),
            verified_at: row?.verified ? row.at : null, verified_detail: row?.detail || "",
            target: row?.target || "", configured_by: row?.by || null, configured_at: row?.at || null,
            token_hint: `A ${name} token.`,
          };
        }),
        open_changes: 0,
        vault_ready: control.vaultReady,
        environment: "local",
        release: { revision: "mock123", git_sha: "mock123" },
      });
      return;
    }
    if (method === "GET" && path === "/api/v3/admin/control/settings") {
      if (!control.platforms.railway) {
        json(response, 200, { source: "process", editable: false, groups: CONTROL_CATALOG });
        return;
      }
      const vars = control.railway.services["svc-gw"].vars;
      const groups = CONTROL_CATALOG.map((group) => ({
        ...group,
        settings: group.settings.map((entry) => ({
          ...entry,
          set: entry.variable in vars,
          raw: entry.secret ? null : (vars[entry.variable] ?? null),
        })),
      }));
      json(response, 200, { source: "railway", editable: true, service_id: "svc-gw", service_name: "gateway", groups });
      return;
    }
    if (method === "GET" && path === "/api/v3/admin/control/changes") {
      json(response, 200, { changes: control.changes });
      return;
    }
    if (method === "POST" && path === "/api/v3/admin/control/step-up/challenge") {
      const nonce = `n${now}${Math.random().toString(36).slice(2, 8)}`;
      control.nonces[nonce] = { sub, expires: now + 300_000 };
      json(response, 200, { nonce, expires_at: new Date(now + 300_000).toISOString(), max_age_s: 300 });
      return;
    }
    if (method === "POST" && path === "/api/v3/admin/control/step-up/revoke") {
      let revoked = 0;
      for (const [grant, row] of Object.entries(control.grants)) {
        if (row.sub === sub) {
          delete control.grants[grant];
          revoked += 1;
        }
      }
      json(response, 200, { revoked });
      return;
    }
    if (method === "POST" && path === "/api/v3/admin/control/step-up") {
      const body = await readBody(request);
      const issued = control.nonces[body.nonce];
      delete control.nonces[body.nonce];
      if (!issued || issued.sub !== sub || body.id_token !== `mock_step_up:${body.nonce}`) {
        auraError(response, 403, "authorization_error", "the re-sign-in was refused", false, {
          reason: "step_up_refused",
        });
        return;
      }
      const grant = `grant-${now}-${Math.random().toString(36).slice(2, 10)}`;
      control.grants[grant] = { sub, expires: now + 300_000 };
      control.stepUps += 1;
      json(response, 200, { grant, expires_at: new Date(now + 300_000).toISOString() });
      return;
    }
    const connectMatch = path.match(/^\/api\/v3\/admin\/control\/platforms\/([a-z_]+)\/(credential|disconnect)$/);
    if (method === "POST" && connectMatch) {
      const [, platformName, op] = connectMatch;
      if (!CONTROL_PLATFORMS.includes(platformName)) {
        auraError(response, 422, "validation_error", "unknown platform");
        return;
      }
      if (!requireGrant()) return;
      const body = await readBody(request);
      if ((body.confirm || "") !== platformName) {
        auraError(response, 422, "validation_error", `type '${platformName}' to confirm`, false, {
          reason: "confirmation_required",
        });
        return;
      }
      if (op === "disconnect") {
        if (!control.platforms[platformName]) {
          auraError(response, 404, "not_found", `${platformName} is not connected`);
          return;
        }
        delete control.platforms[platformName];
        journal(platformName, "disconnect");
        json(response, 200, { platform: platformName, connected: false });
        return;
      }
      if (!control.vaultReady) {
        auraError(response, 422, "validation_error", "the vault key is not set", false, { reason: "vault_not_configured" });
        return;
      }
      if (body.token === "refused-token") {
        auraError(response, 422, "validation_error", `${platformName} refused the token`);
        return;
      }
      const replaced = Boolean(control.platforms[platformName]);
      const verified = platformName !== "grafana";
      control.platforms[platformName] = {
        target: body.target || "", by: who.email, at: new Date(now).toISOString(), verified,
        detail: verified ? `${platformName} verified` : "",
      };
      control.receivedTokens.push(body.token);
      journal(platformName, replaced ? "replace_token" : "connect");
      json(response, 200, { platform: platformName, verified, verified_detail: verified ? `${platformName} verified` : "" });
      return;
    }

    // -- GPU Space (stage 3) ------------------------------------------------------------------
    if (path.startsWith("/api/v3/admin/control/space")) {
      const credential = control.platforms.hf_space;
      if (!credential) {
        auraError(response, 404, "not_found", "the GPU Space is not connected; connect it in Settings");
        return;
      }
      const sp = control.space;
      const spaceId = credential.target || "org/aura-inference";
      const name = spaceId.split("/").pop();
      const refuse = (message, reason) => auraError(response, 422, "validation_error", message, false, { reason });
      const busy = () => control.changes.some((c) => c.target === spaceId && c.status === "watching");
      if (method === "GET" && path === "/api/v3/admin/control/space") {
        json(response, 200, {
          space_id: spaceId, name, stage: sp.stage, hardware: sp.hardware, requested_hardware: sp.hardware,
          sleep_time: sp.sleep_time, hardware_options: SPACE_HARDWARE, sleep_choices: SLEEP_CHOICES,
          change_in_progress: busy(), watch_minutes: 20,
        });
        return;
      }
      if (method === "GET" && path === "/api/v3/admin/control/space/variables") {
        json(response, 200, {
          variables: Object.keys(sp.vars).sort().map((n) => ({ name: n, secret: isSecret(n, sp.vars[n]), value: isSecret(n, sp.vars[n]) ? null : sp.vars[n] })),
        });
        return;
      }
      if (method === "GET" && path === "/api/v3/admin/control/space/secrets") {
        json(response, 200, { secrets: Object.keys(sp.secrets).sort().map((n) => ({ name: n, updated_at: sp.secrets[n].updated_at })) });
        return;
      }
      if (method === "GET" && path === "/api/v3/admin/control/space/logs") {
        const kind = new URL(request.url, "http://mock").searchParams.get("kind") || "run";
        json(response, 200, { lines: sp.logs[kind] || [] });
        return;
      }
      const checkVars = (changes) => {
        const clean = {};
        for (const [n, v] of Object.entries(changes || {})) {
          if (v !== null && isSecret(n, v)) return refuse(`${n} looks secret: add it under the Space's secrets`, "secret_as_variable");
          if (v === null && !(n in sp.vars)) continue;
          if (v !== null && sp.vars[n] === v) continue;
          clean[n] = v;
        }
        if (!Object.keys(clean).length) return refuse("every value is already what you asked for", "no_changes");
        return clean;
      };
      if (method === "POST" && path === "/api/v3/admin/control/space/variables/validate") {
        const body = await readBody(request);
        if (!checkVars(body.changes)) return;
        json(response, 200, { ok: true, variables: [] });
        return;
      }
      if (method !== "POST") {
        auraError(response, 404, "not_found", "no such route");
        return;
      }
      if (!requireGrant()) return;
      const body = await readBody(request);
      if ((body.confirm || "") !== name) {
        refuse(`type '${name}' to confirm`, "confirmation_required");
        return;
      }
      const record = (action, status, detail = {}) => {
        const change = {
          change_id: `chg_${control.changes.length + 1}`, actor: who.email, platform: "hf_space", target: spaceId, action,
          status, detail: { service: name, ...detail }, deployment_id: null, previous_deployment_id: null, reason: "",
          created_at: new Date(now).toISOString(), updated_at: new Date(now).toISOString(),
        };
        control.changes.unshift(change);
        json(response, 200, change);
      };
      const op = path.slice("/api/v3/admin/control/space".length);
      if (op === "/variables") {
        if (busy()) return refuse("a change to the Space is still being watched", "change_in_progress");
        const clean = checkVars(body.changes);
        if (!clean) return;
        for (const [n, v] of Object.entries(clean)) {
          if (v === null) delete sp.vars[n];
          else sp.vars[n] = v;
        }
        sp.stage = "APP_STARTING";
        sp.events.push("variables");
        record("space_variables", "watching", { watch_s: 1200, variables: Object.keys(clean).map((n) => ({ name: n })) });
        return;
      }
      if (op === "/secrets") {
        for (const [n, v] of Object.entries(body.changes || {})) {
          if (v === "") return refuse(`give ${n} a value (or delete it)`, "value_required");
          if (v === null) delete sp.secrets[n];
          else sp.secrets[n] = { value: v, updated_at: new Date(now).toISOString() };
        }
        sp.events.push("secrets");
        record("space_secrets", "recorded", { restorable: false, variables: Object.keys(body.changes).map((n) => ({ name: n, secret: true })) });
        return;
      }
      if (op === "/pause") {
        sp.stage = "PAUSED";
        sp.events.push("pause");
        record("space_pause", "recorded");
        return;
      }
      if (op === "/restart") {
        sp.stage = "APP_STARTING";
        sp.events.push(body.factory ? "factory" : "restart");
        record(body.factory ? "space_rebuild" : "space_restart", "recorded");
        return;
      }
      if (op === "/hardware") {
        if (!SPACE_HARDWARE.includes(body.hardware)) return refuse("unknown Space hardware", "bad_hardware");
        if (body.hardware === sp.hardware) return refuse(`the Space already runs on ${body.hardware}`, "no_changes");
        const from = sp.hardware;
        sp.hardware = body.hardware;
        sp.events.push(`hardware:${body.hardware}`);
        record("space_hardware", "recorded", { from, to: body.hardware });
        return;
      }
      if (op === "/sleep-time") {
        if (!SLEEP_CHOICES.includes(body.seconds)) return refuse("choose one of the offered sleep times", "bad_sleep_time");
        sp.sleep_time = body.seconds;
        sp.events.push(`sleep:${body.seconds}`);
        record("space_sleep_time", "recorded", { to: body.seconds });
        return;
      }
      auraError(response, 404, "not_found", "no such route");
      return;
    }

    // -- Railway (stage 2) --------------------------------------------------------------------
    if (path.startsWith("/api/v3/admin/control/railway")) {
      if (!control.platforms.railway) {
        auraError(response, 404, "not_found", "railway is not connected; connect it in Settings");
        return;
      }
      const rw = control.railway;
      const refuse = (message, reason) =>
        auraError(response, 422, "validation_error", message, false, { reason });
      const openTarget = (id) => control.changes.some((c) => c.target === id && c.status === "watching");
      if (method === "GET" && path === "/api/v3/admin/control/railway") {
        json(response, 200, {
          project: { id: "proj-1", name: "aura" },
          environment: { id: "env-1", name: "production" },
          services: Object.entries(rw.services).map(([id, svc]) => ({
            service_id: id, name: svc.name, latest_deployment_id: svc.deployments[0]?.deployment_id ?? null,
            latest_status: svc.deployments[0]?.status ?? null, latest_at: svc.deployments[0]?.created_at ?? null,
            healthcheck_path: svc.healthcheck, is_gateway: id === "svc-gw", change_in_progress: openTarget(id),
          })),
        });
        return;
      }
      const m = path.match(/^\/api\/v3\/admin\/control\/railway\/services\/([^/]+)(\/.*)$/);
      const svc = m && rw.services[m[1]];
      if (!m || !svc) {
        auraError(response, 404, "not_found", "this environment has no such Railway service");
        return;
      }
      const [, id, rest] = m;
      if (method === "GET" && rest === "/variables") {
        json(response, 200, {
          variables: Object.keys(svc.vars).sort().map((name) => {
            const value = svc.vars[name];
            const secret = isSecret(name, value);
            return { name, secret, value: secret ? null : value, reference: value.includes("${{"),
              protected: PROTECTED.has(name), provided: name.startsWith("RAILWAY_") };
          }),
        });
        return;
      }
      if (method === "GET" && rest === "/deployments") {
        json(response, 200, { deployments: svc.deployments });
        return;
      }
      const logs = rest.match(/^\/deployments\/([^/]+)\/logs$/);
      if (method === "GET" && logs) {
        const kind = new URL(request.url, "http://mock").searchParams.get("kind") || "deploy";
        const secrets = Object.entries(svc.vars).filter(([k, v]) => isSecret(k, v)).map(([, v]) => v);
        const lines = (rw.logs[`${kind}:${logs[1]}`] || []).map((l) => ({
          ...l, message: secrets.reduce((text, v) => text.split(v).join("[redacted]"), l.message),
        }));
        json(response, 200, { lines });
        return;
      }
      // What the engine checks before anything is written (control/railway.py `_check`).
      const check = (changes) => {
        const names = Object.keys(changes || {});
        if (!names.length) return refuse("there is nothing to change", "no_changes");
        const clean = {};
        for (const name of names) {
          const value = changes[name];
          if (PROTECTED.has(name)) return refuse(`${name} can only be changed in the Railway dashboard`, "protected_variable");
          if (name.startsWith("RAILWAY_")) return refuse(`${name} is provided by Railway`, "provided_variable");
          if (value !== null && value.includes("${{")) return refuse("references are set in the Railway dashboard", "reference_not_supported");
          if (value === null && !(name in svc.vars)) continue;
          if (value !== null && svc.vars[name] === value) continue;
          clean[name] = value;
        }
        if (!Object.keys(clean).length) return refuse("every value is already what you asked for", "no_changes");
        const runsAura = Object.keys(svc.vars).some((k) => k.startsWith("AURA_"));
        if (runsAura && !svc.healthcheck) return refuse(`${svc.name} has no health-check path`, "healthcheck_not_configured");
        const topK = clean.AURA_KNOWLEDGE_TOP_K;
        if (runsAura && topK !== undefined && topK !== null && Number(topK) > 50) {
          return refuse("the gateway would not start with this change: AURA_KNOWLEDGE_TOP_K must be <= 50", "settings_invalid");
        }
        return { clean, runsAura };
      };
      if (method === "POST" && rest === "/variables/validate") {
        const body = await readBody(request);
        const ok = check(body.changes);
        if (!ok) return;
        json(response, 200, { ok: true, settings_checked: ok.runsAura, variables: Object.keys(ok.clean).map((name) => ({ name })) });
        return;
      }
      if (method !== "POST") {
        auraError(response, 404, "not_found", "no such route");
        return;
      }
      if (!requireGrant()) return;
      const body = await readBody(request);
      if ((body.confirm || "") !== svc.name) {
        refuse(`type '${svc.name}' to confirm`, "confirmation_required");
        return;
      }
      const record = (action, status, detail, deploymentId = null) => {
        const change = {
          change_id: `chg_${control.changes.length + 1}`, actor: who.email, platform: "railway", target: id, action,
          status, detail: { service: svc.name, ...detail }, deployment_id: deploymentId, previous_deployment_id: null, reason: "",
          created_at: new Date(now).toISOString(), updated_at: new Date(now).toISOString(),
        };
        control.changes.unshift(change);
        json(response, 200, change);
      };
      const deploy = () => {
        const deploymentId = `dep-${++rw.seq}`;
        svc.deployments.unshift({ deployment_id: deploymentId, status: "DEPLOYING", created_at: new Date(now).toISOString(),
          can_rollback: true, can_redeploy: true, commit: "85258222c84c", message: "redeploy" });
        return deploymentId;
      };
      if (rest === "/variables") {
        if (openTarget(id)) return refuse(`a change to ${svc.name} is still being watched`, "change_in_progress");
        const ok = check(body.changes);
        if (!ok) return;
        for (const [name, value] of Object.entries(ok.clean)) {
          if (value === null) delete svc.vars[name];
          else svc.vars[name] = value;
        }
        rw.applied.push(Object.keys(ok.clean));
        record("set_variables", "watching", {
          variables: Object.entries(ok.clean).map(([name, value]) => ({ name, secret: isSecret(name, value), deleted: value === null })),
        }, deploy());
        return;
      }
      if (rest === "/redeploy") {
        if (openTarget(id)) return refuse(`a change to ${svc.name} is still being watched`, "change_in_progress");
        record("redeploy", "watching", {}, deploy());
        return;
      }
      if (rest === "/restart") {
        rw.restarts.push(svc.deployments[0].deployment_id);
        record("restart", "recorded", { deployment_id: svc.deployments[0].deployment_id });
        return;
      }
      if (rest === "/rollback") {
        if (!svc.deployments.some((d) => d.deployment_id === body.deployment_id)) {
          auraError(response, 404, "not_found", "that deployment is not one of this service's last 50");
          return;
        }
        record("rollback", "recorded", { to_deployment_id: body.deployment_id }, deploy());
        return;
      }
      auraError(response, 404, "not_found", "no such route");
      return;
    }
  }

  if (method === "GET" && path === "/api/v3/admin/status") {
    if (!coreGate(response, who, "Admin")) return;
    json(response, 200, {
      health: { healthy: true, environment: "local", index: "aura-knowledge__v1__hash__32" },
      providers: [
        {
          provider: "echo",
          state: "closed",
          healthy: true,
          inflight: 0,
          requests: 4,
          failures: 0,
          failure_rate: 0,
          latency_ms: 12,
          ttft_ms: 8,
          circuit_opens: 0,
        },
      ],
      prompts: [
        {
          id: "system.orchestrator",
          versions: ["1.0.0", "1.1.0"],
          active: "1.1.0",
          pinned: state.promptPin,
          checksum: "abc",
        },
      ],
      tools: [],
      settings: { security: { allow_mock_auth: true } },
      learning: { total_records: 3 },
      refresh: null,
      providers_scope: "worker",
      worker_pid: 4242,
    });
    return;
  }

  if (method === "POST" && path === "/api/v3/admin/providers/echo/reset-circuit") {
    if (!coreGate(response, who, "Admin")) return;
    json(response, 200, {
      reset: "echo",
      scope: "worker",
      worker_pid: 4242,
      note: "Other gateway workers keep their own breaker state.",
    });
    return;
  }

  if (method === "POST" && path === "/api/v3/admin/prompts/system.orchestrator/pin") {
    if (!coreGate(response, who, "Admin")) return;
    const body = await readBody(request);
    if (typeof body.version !== "string") {
      json(response, 422, {
        detail: [{ type: "missing", loc: ["body", "version"], msg: "Field required", input: body }],
      });
      return;
    }
    state.promptPin = body.version;
    json(response, 200, { pinned: `system.orchestrator@${body.version}`, shared: true, propagation_s: 5 });
    return;
  }

  // Releases a pin (sibling backend change): 200, or 404 for an unknown template.
  const unpin = path.match(/^\/api\/v3\/admin\/prompts\/([^/]+)\/pin$/);
  if (method === "DELETE" && unpin) {
    if (!coreGate(response, who, "Admin")) return;
    if (unpin[1] !== "system.orchestrator") {
      auraError(response, 404, "not_found", `unknown prompt template ${unpin[1]}`);
      return;
    }
    state.promptPin = null;
    json(response, 200, { unpinned: unpin[1], shared: true, propagation_s: 5 });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/models") {
    if (!coreGate(response, who, "Admin")) return;
    json(response, 200, { models: [] });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/local-models") {
    if (!coreGate(response, who, "Admin")) return;
    json(response, 200, { available: true, models: state.localModels });
    return;
  }

  if (method === "POST" && path === "/api/v3/admin/local-models/pull") {
    if (!coreGate(response, who, "SuperAdmin")) return;
    const body = await readBody(request);
    json(response, 200, { pulled: body.name, progress: {} });
    return;
  }

  // `{name:path}` on the real route: the name may contain '/'.
  if (method === "DELETE" && path.startsWith("/api/v3/admin/local-models/")) {
    if (!coreGate(response, who, "SuperAdmin")) return;
    const name = path.slice("/api/v3/admin/local-models/".length);
    state.localModelDeletes.push(rawPath);
    const before = state.localModels.length;
    state.localModels = state.localModels.filter((row) => row.name !== name);
    json(response, 200, { deleted: state.localModels.length < before });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/sources") {
    if (!coreGate(response, who, "Admin")) return;
    json(response, 200, { sources: state.sources });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/indexes") {
    if (!coreGate(response, who, "Admin")) return;
    json(response, 200, { indexes: [] });
    return;
  }

  if (method === "PATCH" && path.startsWith("/api/v3/admin/sources/")) {
    if (!coreGate(response, who, "Admin")) return;
    const id = path.slice("/api/v3/admin/sources/".length);
    const body = await readBody(request);
    if (id === "flaky") {
      auraError(response, 503, "store_unavailable", "the source registry is unavailable", true);
      return;
    }
    const source = state.sources.find((row) => row.id === id);
    if (source) {
      source.enabled = Boolean(body.enabled);
    }
    json(response, 200, { source: source ?? { id } });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/approvals") {
    if (!coreGate(response, who, "Admin")) return;
    const status = url.searchParams.get("status");
    const kind = url.searchParams.get("kind");
    json(response, 200, {
      approvals: state.approvals.filter(
        (row) => (!status || row.status === status) && (!kind || row.kind === kind),
      ),
    });
    return;
  }

  const decide = path.match(/^\/api\/v3\/admin\/approvals\/([^/]+)\/decide$/);
  if (method === "POST" && decide) {
    if (!coreGate(response, who, "Admin")) return;
    const row = state.approvals.find((item) => item.id === decide[1]);
    if (!row) {
      auraError(response, 404, "not_found", `no approval ${decide[1]}`);
      return;
    }
    if (SUPER_ADMIN_APPROVAL_KINDS.has(row.kind) && !coreGate(response, who, "SuperAdmin")) return;
    const body = await readBody(request);
    if (row.status !== "pending") {
      auraError(response, 409, "conflict", `approval ${row.id} is already ${row.status}`);
      return;
    }
    row.status = body.approved ? "approved" : "rejected";
    row.decided_by = who.email;
    json(response, 200, { approval: row });
    return;
  }

  // aura_core/api/billing.py: both SuperAdmin-only (SaaS P1-F1).
  if (method === "GET" && path === "/api/v3/admin/customers") {
    if (!coreGate(response, who, "SuperAdmin")) return;
    state.customerLists += 1;
    // MOCK_CUSTOMER_PAGE caps the page so "Load more" is exercised with four rows.
    const cap = Number(process.env.MOCK_CUSTOMER_PAGE || 3);
    const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit") || 50), 200, cap));
    const after = url.searchParams.get("after");
    const page = state.customers.filter((row) => !after || row.workspace_id > after).slice(0, limit);
    // As BillingService.customers: a full page always names a cursor, even when nothing follows.
    json(response, 200, {
      customers: page,
      next: page.length === limit ? page[page.length - 1].workspace_id : null,
    });
    return;
  }

  const grace = path.match(/^\/api\/v3\/admin\/customers\/([^/]+)\/extend-grace$/);
  if (method === "POST" && grace) {
    if (!coreGate(response, who, "SuperAdmin")) return;
    state.graceCalls += 1;
    const body = await readBody(request);
    if (!Number.isInteger(body.days) || body.days < 1 || body.days > 30) {
      auraError(response, 422, "validation_error", "days must be between 1 and 30");
      return;
    }
    const row = state.customers.find((item) => item.workspace_id === grace[1]);
    if (!row || row.provider !== "fawry" || !["past_due", "grace", "expired"].includes(row.status)) {
      auraError(response, 409, "conflict", "only a Fawry subscription that is past due, in grace or expired can get more grace");
      return;
    }
    if (row.last_payment?.status === "refunded") {
      auraError(response, 409, "conflict", "this subscription's last payment was refunded");
      return;
    }
    // grace_until, or period_end plus the default 7 grace days, never before "now" (2026-11-18).
    const base = Math.max(Date.parse(row.grace_until || row.period_end) + (row.grace_until ? 0 : 7 * 86_400_000), Date.parse("2026-11-18T00:00:00Z"));
    row.status = "grace";
    row.grace_until = new Date(base + body.days * 86_400_000).toISOString();
    json(response, 200, { status: "grace", grace_until: row.grace_until });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/operators") {
    if (!platformGate(response, who, "Admin")) return;
    json(response, 200, { operators: state.operators });
    return;
  }

  const roleMatch = path.match(/^\/api\/v3\/admin\/operators\/([^/]+)\/role$/);
  if (method === "PATCH" && roleMatch) {
    if (!platformGate(response, who, "SuperAdmin")) return;
    const email = roleMatch[1];
    const body = await readBody(request);
    if (!(body.role in RANK)) {
      json(response, 422, { detail: { allowed: Object.keys(RANK) } });
      return;
    }
    const target = state.operators.find((row) => row.email === email);
    if (!target) {
      json(response, 404, {
        detail: "no user with that email -- they must sign in once before a role can be assigned",
      });
      return;
    }
    const floor = ENV_GRANTS[email];
    if (floor && RANK[body.role] < RANK[floor[0]]) {
      json(response, 409, {
        detail: `${email} is granted ${floor[0]} by ${floor[1]}; remove them there to lower their role`,
      });
      return;
    }
    if (target.role === "SuperAdmin" && body.role !== "SuperAdmin") {
      if (superAdminCount() <= 1) {
        json(response, 403, { detail: "cannot demote the last SuperAdmin" });
        return;
      }
      if (who.email === email) {
        json(response, 403, { detail: "cannot change your own SuperAdmin role" });
        return;
      }
    }
    target.role = body.role;
    target.granted_by = "Access page";
    json(response, 200, {
      email,
      role: body.role,
      granted_by: "Access page",
      revoked: { sessions: 0, devices: 0 },
    });
    return;
  }

  const revokeMatch = path.match(/^\/api\/v3\/admin\/operators\/([^/]+)\/revoke-sessions$/);
  if (method === "POST" && revokeMatch) {
    if (!platformGate(response, who, "SuperAdmin")) return;
    const email = revokeMatch[1];
    if (!state.operators.some((row) => row.email === email)) {
      json(response, 404, { detail: "operator not found" });
      return;
    }
    state.revoked.push(email);
    json(response, 200, { email, revoked: 1 });
    return;
  }

  const forgetMatch = path.match(/^\/api\/v3\/admin\/users\/([^/]+)\/forget$/);
  if (method === "POST" && forgetMatch) {
    if (!platformGate(response, who, "SuperAdmin")) return;
    const notErased = [
      "feedback records",
      "agent run history",
    ];
    // A store that failed mid-erasure: the real handler keeps the users row and says so.
    if (forgetMatch[1] === "partial@example.com") {
      json(response, 200, {
        forgotten: false,
        counts: { sessions: 1, memories: 3 },
        failed: ["documents"],
        not_erased: notErased,
      });
      return;
    }
    json(response, 200, {
      forgotten: true,
      counts: { sessions: 1, sync_rows: 4, user: 1 },
      failed: [],
      not_erased: notErased,
    });
    return;
  }

  json(response, 404, { detail: "Not Found" });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock-gateway listening on ${PORT}\n`);
});
