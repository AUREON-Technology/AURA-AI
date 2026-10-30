import http from "node:http";

const PORT = Number(process.env.MOCK_GATEWAY_PORT || 8010);

const principals = {
  mock_token: {
    uid: "a1",
    email: "ops@aura.ai",
    name: "Ops",
    role: "Admin",
    is_admin: true,
    is_super_admin: false,
    access_token: "admin-access",
  },
  mock_token_super: {
    uid: "s1",
    email: "root@aura.ai",
    name: "Root",
    role: "SuperAdmin",
    is_admin: true,
    is_super_admin: true,
    access_token: "super-access",
  },
};

const tokens = {
  "admin-access": principals.mock_token,
  "super-access": principals.mock_token_super,
};

// Refresh tokens are single use, as on the real gateway (jwt_auth._refresh_store_take): the
// first /auth/refresh deletes the token, and any replay of it is refused with 401.
const refreshTokens = {};
const refreshReplays = {};
let issued = 0;

function issuePair(principal) {
  issued += 1;
  const access = `${principal.access_token}-${issued}`;
  const refresh = `${principal.access_token}-refresh-${issued}`;
  tokens[access] = principal;
  refreshTokens[refresh] = principal;
  return { token_type: "bearer", access_token: access, refresh_token: refresh, expires_in: 900 };
}

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
  ],
};

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
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
  return tokens[token] || principals[token] || null;
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || "/", "http://127.0.0.1");
  const path = url.pathname;
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

  // Test hooks: expire one access token (as if its 15 minutes ran out) and report how many
  // times a spent refresh token was replayed.
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

  if (!who) {
    json(response, 401, { detail: { error: "unauthenticated", message: "missing token" } });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/me") {
    json(response, 200, who);
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/status") {
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
          pinned: null,
          checksum: "abc",
        },
      ],
      tools: [],
      settings: { security: { allow_mock_auth: true } },
      learning: { total_records: 3 },
      refresh: null,
    });
    return;
  }

  if (method === "POST" && path === "/api/v3/admin/providers/echo/reset-circuit") {
    json(response, 200, { reset: "echo" });
    return;
  }

  if (method === "POST" && path === "/api/v3/admin/prompts/system.orchestrator/pin") {
    json(response, 200, { pinned: "system.orchestrator@1.0.0" });
    return;
  }

  if (method === "GET" && (path === "/api/v3/admin/models" || path === "/api/v3/admin/local-models")) {
    json(response, 200, { models: [] });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/approvals") {
    json(response, 200, { approvals: state.approvals });
    return;
  }

  if (method === "POST" && path === "/api/v3/admin/approvals/appr-1/decide") {
    state.approvals[0].status = "approved";
    json(response, 200, { approval: state.approvals[0] });
    return;
  }

  if (method === "GET" && path === "/api/v3/admin/operators") {
    json(response, 200, { operators: state.operators });
    return;
  }

  if (method === "PATCH" && path.startsWith("/api/v3/admin/operators/") && path.endsWith("/role")) {
    const email = decodeURIComponent(path.split("/")[5]);
    const body = await readBody(request);
    const target = state.operators.find((row) => row.email === email);
    if (!target) {
      json(response, 404, { detail: "operator not found" });
      return;
    }
    const remaining = state.operators.filter((row) => row.role === "SuperAdmin").length;
    if (target.role === "SuperAdmin" && body.role !== "SuperAdmin" && remaining <= 1) {
      json(response, 403, { detail: "cannot demote the last SuperAdmin" });
      return;
    }
    target.role = body.role;
    target.granted_by = "Access page";
    json(response, 200, { email, role: body.role, granted_by: "Access page" });
    return;
  }

  if (method === "POST" && path.includes("/forget")) {
    json(response, 200, { forgotten: true, counts: { sessions: 1, sync_rows: 4, user: 1 } });
    return;
  }

  json(response, 404, { detail: `unhandled ${method} ${path}` });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`mock-gateway listening on ${PORT}\n`);
});
