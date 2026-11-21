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

const state = {
  control: { platforms: {}, changes: [], nonces: {}, grants: {}, stepUps: 0, receivedTokens: [], vaultReady: true },
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
    });
    return;
  }
  if (method === "POST" && path === "/__test/reset-control") {
    const vaultReady = !new URL(request.url, "http://mock").searchParams.has("no-vault");
    state.control = { platforms: {}, changes: [], nonces: {}, grants: {}, stepUps: 0, receivedTokens: [], vaultReady };
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
      json(response, 200, { source: "process", groups: CONTROL_CATALOG });
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
      const verified = !["railway", "grafana"].includes(platformName);
      control.platforms[platformName] = {
        target: body.target || "", by: who.email, at: new Date(now).toISOString(), verified,
        detail: verified ? `${platformName} verified` : "",
      };
      control.receivedTokens.push(body.token);
      journal(platformName, replaced ? "replace_token" : "connect");
      json(response, 200, { platform: platformName, verified, verified_detail: verified ? `${platformName} verified` : "" });
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
