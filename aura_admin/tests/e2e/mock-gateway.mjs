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

const state = {
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
};

const SUPER_ADMIN_APPROVAL_KINDS = new Set(["connector_registration", "blueprint_deployment"]);

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

// AuraError.to_dict() raised as an HTTPException detail: v3's handler puts it at the top level
// and keeps it under `detail` (aura_core/api/v3.py, wrapped_domain_error_handler).
function auraError(response, status, error, message, retryable = false) {
  const body = { error, message, retryable, details: {} };
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

  if (method === "GET" && path === "/__test/refresh-replays") {
    json(response, 200, { replays: Object.values(refreshReplays).reduce((a, b) => a + b, 0) });
    return;
  }

  if (method === "GET" && path === "/__test/local-model-deletes") {
    json(response, 200, { paths: state.localModelDeletes });
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
