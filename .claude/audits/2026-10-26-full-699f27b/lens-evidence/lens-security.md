# Security lens — AURA-AI @ 699f27b

Read-only review. No repo files modified. Secret values redacted. All file:line cites are from files opened during this review.
Paths below are relative to `/home/user/AURA-AI/`.

## 1. Backlog item re-verification

| Item | Status | Evidence (current code) |
|---|---|---|
| **CB-04** tool confirmation not bound to the approved call | **OPEN** | `aura_backend/aura_core/execution/engine.py:475-489`: `call_id = f"step_{step.id.value}"`; `confirmed = call_id in confirmed_call_ids or step.tool in confirmed_tools`. Step ids are positional: `aura_core/planning/engine.py:313` `self_id = f"s{index + 1}"`. Resume re-plans from scratch: `kernel.py:1250-1256` forces planning whenever confirmations are present, and no server-side pending-approval store exists (grep for approval token or argument digest finds only the AP connector code). Client sends **both** names and ids, so the name fallback is always active: `aura_client/lib/features/chat/presentation/chat_view_model.dart:136-146` and `.../data/chat_repository.dart:253-282`, which resends the last user message. The voice path is worse: `aura_core/voice/session.py:162-168` confirms by tool name only and re-runs the turn with `message="continue"`, and `api/voice_live.py:206-219` passes `frame["tools"]` straight through. |
| **CB-05** `workspace_local` exemption when routed to aura_hostd | **OPEN** | `aura_core/tools/policy.py:114-115` allows any `workspace_local and side_effecting` call before the taint check at `:117`. The routing to the user's real machine is decided in `tools/coding.py:369-383` (`_desktop_active`), and policy never sees it. In hostd, `command_policy.dart:112-113` allows every non-denylisted command in a trusted root. `evaluateWrite` (`:159-166`) has no path denylist, and its own doc (`:150-158`) says `.git/hooks/` is not covered. The untrusted-tier allowlist can itself execute code (see SEC-01). |
| **CB-06** hardcoded `GOCSPX-…` secret | **FIXED in code / PARTIAL** | `git grep -n GOCSPX` now finds only comments, docs and the test pattern. `aura_client/lib/core/config/config.dart:57-60` has `defaultValue: ''`. The guard is `aura_backend/tests/architecture/test_no_committed_client_secrets.py:42-80`. It scans only `aura_client/**/*.dart`, not `aura_hostd/` (which the backlog asked for) or non-Dart files. Rotation at Google cannot be verified from the repo, and the old value is still in git history (`cad6e7a`). |
| **CB-07** `secrets.*` in job-level `if:` | **FIXED in code** | No `if:` uses `secrets.` in any workflow (grep is empty). `control-plane-deploy.yml:45,92-94` and `inference-plane-deploy.yml:65,107,155` decide inside a preflight step and gate on `needs.preflight.outputs.configured`. Whether runs are green is not verified here (no network). |
| **CB-08** backup mounts all of `deploy/secrets`; dev shares prod keys | **OPEN** | `deploy/docker-compose.prod.yml:1157`: `./secrets:/run/backup-secrets:ro` mounts the whole directory, including `data_encryption_keys` and `jwt_secret`. The dev file lives in the same `deploy/` dir and declares the same files: `docker-compose.dev.yml:170-181` (`./secrets/jwt_secret`, `./secrets/data_encryption_keys`), against prod `:208,240`. Impact is worse than the backlog says; see SEC-03. |
| **CB-10** no JWT_SECRET startup refusal | **OPEN** | `services/jwt_auth.py:53-66`: a per-process random fallback, with only a warning. Nothing in `aura_core/config/` or `config.py:137-140` validates presence or length (grep for `jwt` in `aura_core/config/` is empty). With `--workers 4`, each worker would sign with a different key. |
| **CB-12** limiter IP / Content-Length trust / body-limit mismatch | **PARTIAL** | The client-IP part is mitigated in the shipped topology. nginx *sets* `X-Forwarded-For $remote_addr` (`deploy/nginx/conf.d/proxy_headers.inc:9-10`), uvicorn runs `--proxy-headers --forwarded-allow-ips='*'` (`docker-compose.prod.yml:360`), and the v3 limiter uses `extract_client_ip(... trusted_proxies=...)` (`api/v3.py:297-305`). Still open: `infrastructure/http_limits.py:89-104` only checks `Content-Length` (a chunked body bypasses it), and nginx allows 20 MB (`aura.conf.template:125`) against the gateway's 5 MB (`aura_service.py` `_apply_http_limits`). An upload between 5 and 20 MB that sends Content-Length gets a 413 from the gateway even when `knowledge.max_document_bytes` would accept it (`v3.py` upload, `len(data) > max_document_bytes`). Also, `--forwarded-allow-ips='*'` trusts XFF from *any* container on the gateway's networks, not just nginx. The template comment at `aura.conf.template:13-15` says "exactly one proxy is trusted", which is not what the flag does. |
| GAP-048 cross-user session overwrite | **FIXED** | `aura_core/memory/session.py:161-174`: the key is namespaced by owner. `load` refuses a foreign owner (`:186-194`). Pinned by `tests/security/test_idor.py` (referenced in the docstring). |
| GAP-048 refresh-token double spend | **FIXED** | `services/jwt_auth.py:83-100` uses atomic `GETDEL`, with a locked pop in-process. Residual: there is no reuse detection or family revocation (an opportunity, not a defect). |
| GAP-048 WS reauth as another user | **FIXED** | `api/host_agent.py:109-119` and `api/voice_live.py:223-235` close the socket when `renewed.user_id != principal.user_id`. Residual: tokens expire mid-session without effect (SEC-06). |
| GAP-048 SSRF in URL-fetching tools | **FIXED, residual documented** | `tools/builtin.py:385-412`: https only, allowlist, IP-literal check. `:415-440` resolves before fetch, and `:478-486` turns redirects off. `domain/network.py:15-38` is shared with `learning/sources.py` and `adapters/connectors/openapi.py:94-107`. Residual (acknowledged in the docstring at `:422-424`): a DNS-rebinding TOCTOU between `getaddrinfo` and the httpx connect. `response.text` is also loaded without a byte cap before the 20k-char truncation. |
| CB-11 (context) Qdrant owner-less points | FIXED in code (per backlog 2026-10-12 update; not re-probed) | — |

## 2. Findings

### DEFECTS

#### SEC-01 — hostd "read-only" allowlist runs arbitrary code in *untrusted* workspaces without approval
- **Type:** Defect (authorization bypass / RCE on user machine) · **Severity:** High · **Evidence:** CODE-VERIFIED
- **Files:** `aura_hostd/lib/src/command_policy.dart:58,74-76,100-140`; `aura_hostd/lib/src/task_handler.dart:255-276`; `aura_backend/aura_core/tools/policy.py:114-115`
- **Trigger:** The server sends `run_command` with any of these commands for an untrusted root (the default for every granted root):
  - `rg --pre ./x.sh pattern`: `--pre` runs a program on every file it searches.
  - `npm test`: runs `scripts.test` from the repo's `package.json`.
  - `pytest --collect-only` / `python -m pytest ... --collect-only`: imports `conftest.py` and the test modules, which executes their code.
  - `git log --output=/abs/path` / `git diff --output=…`: writes a file anywhere on disk.
  - `cat ~/.ssh/id_rsa`, `cat <hostd config dir>/control.json`: reads anything outside the root. The control-server bearer token lives in `control.json`, written with the default umask at `control_server.dart:119-122`, and that token can flip `trust` (`control_server.dart:270-272`).
  
  None of these contains a character in `[;&|`$<>\n]`, so each passes the metacharacter floor, matches a prefix pattern, and returns `CommandDecision.allow` with no approval queued.
- **Actual vs expected:** The module doc (`:1-13`, `:60-63`) promises that the untrusted tier only runs "read-only/inspection commands that are safe to run without asking". In fact, three allowlisted verbs execute repo-supplied code and one writes arbitrary paths.
- **Impact:** CB-05 means the server applies no confirmation to `run_command`, tainted or not. That leaves this allowlist as the *only* gate between prompt-injected content (for example a cloned repo's README, or `fetch_url` output) and code execution as the user's OS account. A malicious repo cloned into an untrusted root is exactly the case this tier exists for.
- **Root cause:** Commands are classified by verb prefix, but `npm`, `pytest` and `rg` have flags or semantics that execute code.
- **Minimal fix:**
  - Drop `npm test`, the `pytest --collect-only` entries, `rg`, and `git (diff|log|show)` from the untrusted allowlist, or reject them when they carry `--pre`, `--output`, `--ext-diff` or `--textconv`.
  - Make `cat`, `head`, `tail`, `grep` and `rg` require their path arguments to be contained (tokenize argv and call `requireContained` on anything that is not a flag).
  - Write `control.json` with mode 0600.
- **Regression test:** In `command_policy_test.dart`, assert `evaluateCommand(x, workspaceTrusted:false).decision != allow` for `rg --pre ./x a`, `npm test`, `pytest . --collect-only`, `git log --output=/tmp/x`, and `cat /etc/passwd`.
- **Confidence:** High for `rg --pre`, `npm test`, `pytest` and `git --output`. These are standard behaviors of those tools, and the regexes were read directly.

#### SEC-02 — Operator role demotion has no effect: the JWT `role` claim is sticky through refresh, and the DB role is never read
- **Type:** Defect (authorization / revocation) · **Severity:** High · **Evidence:** CODE-VERIFIED
- **Files:** `aura_backend/services/rbac.py:81-98`; `services/jwt_auth.py:118-131,226-235,301-308`; `api/admin_platform.py:110-137`; `infrastructure/db/user_repo.py:26-34`
- **Trigger:**
  1. A user holds Admin or SuperAdmin (from `ADMIN_EMAILS`, `roles.json`, or a prior DB promotion).
  2. A SuperAdmin demotes them on the Access page (`user.role = payload.role`, `admin_platform.py:136`), or the operator removes them from the env list.
  3. The user keeps calling `/api/v3/auth/refresh`.
- **Actual:**
  - `get_user_role` treats the *token's own* `role` claim as a candidate (`rbac.py:85-87`) and returns the max.
  - `refresh_token_pair` re-stamps from the stored `user_info`, which still carries the old `role` (`jwt_auth.py:233-235`). The elevated role therefore survives every rotation, indefinitely (30-day rolling refresh).
  - `db_role`/`persisted_role` (`rbac.py:82`) is never populated anywhere (grep shows the only reference is `rbac.py:82` itself). The DB role is write-only with respect to auth, so the Access page's demote and promote do nothing to effective privilege.
  - The demote handler does not call `revoke_all_for_uid` either (only `/revoke-sessions` and `/forget` do).
  - Device tokens (`/desktop/login`, pairing) embed the role for 30 days with no refresh at all.
- **Expected:** Role is resolved from authoritative sources (env, roles.json, DB) on every refresh, never from a claim the server itself minted earlier. A demotion revokes or invalidates sessions.
- **Impact:**
  - A demoted or removed admin keeps admin and super-admin capabilities (`aura_service.py` maps role to `admin.act`/`admin.super`).
  - The docstring's guarantee that "SUPERADMIN_EMAILS is the only env list that can mint SuperAdmin" is violated after the first mint.
  - The last-SuperAdmin guard (`admin_platform.py:117-133`) is moot.
- **Root cause:** A token claim is used as an input to authorization (circular trust). The persisted role is never read.
- **Minimal fix:**
  - In `get_user_role`, ignore `user_info["role"]` when it came from a token or refresh store. Read the DB role by `auth_subject` instead.
  - In `refresh_token_pair`, pop `role` before `stamp_resolved_role`.
  - Call `revoke_all_for_uid` and revoke device tokens on demote.
- **Regression test:**
  1. Issue a pair for an email in `ADMIN_EMAILS`.
  2. Remove the email from env.
  3. Refresh.
  4. Assert the new access token's `role == "User"` and `/api/v3/admin/status` returns 403.
  5. Separately: promote or demote via the Access route, then refresh, and assert the effective role follows the DB.
- **Confidence:** High.

#### SEC-03 — Dev stack holds the prod JWT signing key; tokens carry no `iss`/`aud`, so a dev-minted (or dev-forged) token is a valid prod token
- **Type:** Defect (environment isolation) · **Severity:** High · **Evidence:** CODE-VERIFIED (config); deployment state UNVERIFIED (assumes the documented `/opt/aura-stack` layout with both projects using `deploy/secrets`)
- **Files:** `deploy/docker-compose.dev.yml:170-171,251,287,297`; `deploy/docker-compose.prod.yml:208`; `aura_backend/services/jwt_auth.py:118-131,285`; `deploy/nginx/templates/aura.conf.template:289,312,353` (the dev domain is publicly proxied)
- **Trigger:** Any of these:
  - A token issued by the dev gateway. Dev runs `AURA_ENVIRONMENT=development` with `ALLOW_MOCK_ATTESTATION` defaulting to `true`, and may carry different `SUPERADMIN_EMAILS`, `roles.json` or code.
  - Any compromise of a dev container, which runs less-reviewed dev code and can read `/run/secrets/jwt_secret`.
- **Actual:** `jwt.decode(token, _secret(), algorithms=[HS256])` checks neither issuer, audience nor environment. With the shared secret, a dev token with any `sub` and `role: "SuperAdmin"` is accepted by prod. Because of SEC-02, the claimed role is honored.
- **Expected:** Separate signing keys per stack, plus `iss`/`aud` bound to the environment and verified.
- **Impact:** Dev compromise means forging prod SuperAdmin for any user id (full data access, `/users/{subject}/forget`, model and blueprint deploys). The same shared directory also gives dev the prod `data_encryption_keys` (encryption-at-rest key), Postgres and Redis passwords, and Firebase credentials. This upgrades the backlog's CB-08 from "hygiene" to a direct escalation path.
- **Root cause:** Both compose files resolve `./secrets/*` against the same `deploy/` directory. The JWT has no audience binding.
- **Minimal fix:**
  - Point the dev compose at `./secrets-dev/` (and have `bootstrap-secrets.sh` generate it).
  - Add `iss=AURA_ENVIRONMENT` and `aud="aura-api"` claims, and verify them.
  - Add a compose parity test asserting that the dev and prod `jwt_secret` and `data_encryption_keys` file paths differ.
- **Regression test:**
  - A unit test that `verify_access_token` rejects a token with the wrong `iss`.
  - A compose test, alongside `tests/unit/test_hosted_provider_compose_plumbing.py`, parsing both files and asserting the secret sources differ.
- **Confidence:** High for the code and config. Medium on whether the live VPS has overridden the paths.

#### SEC-04 — GitHub OAuth account-linking CSRF: an attacker's `state` binds the victim's GitHub token to the attacker's AURA account
- **Type:** Defect (OAuth CSRF / account takeover of a third-party identity) · **Severity:** Medium-High · **Evidence:** CODE-VERIFIED (flow); the GitHub silent re-authorize behavior is external and PARTIALLY VERIFIED
- **Files:** `aura_backend/api/v2/github_integration.py:72-95,97-150`; `services/github_oauth_state.py:31-47`
- **Trigger:**
  1. The attacker calls `GET /api/v3/integrations/github/connect` with their own JWT. `state` is bound to the attacker's uid (`:84-85`).
  2. The attacker sends the returned `authorize_url` to a victim who is logged into GitHub. GitHub skips the consent screen for an app the user already authorized.
  3. GitHub redirects the victim's browser to `/callback?code=…&state=…`. The callback takes the subject purely from `state` (`:108`), exchanges the code, and stores the *victim's* GitHub access and refresh tokens on the attacker's account (`:120-142`).
- **Expected:** The callback is bound to the initiating client, for example by finishing the exchange from the authenticated client (deep link, then an authenticated POST of code and state), or by a PKCE verifier only the initiating client holds. Alternatively, refuse when `github_user_id` is already linked to another AURA user.
- **Impact:** The attacker's Engineer agent (given `engineer_agent`) acts on the victim's repositories, including GitHub write tools (those are confirmation-gated, but the attacker is the one confirming).
- **Root cause:** A bearer-authenticated initiation is combined with an unauthenticated completion, with no binding between the two browsers.
- **Minimal fix:** Use PKCE: `/connect` returns a `code_verifier` to the client only, and the client posts the code to an authenticated `/complete` endpoint. Also enforce a unique `github_user_id` across users.
- **Regression test:** Simulate `/connect` as user A, then complete `/callback` with a code that maps to GitHub identity X when X is already linked to user B. Assert refusal. Also assert the callback alone can no longer attach tokens without an authenticated completion.
- **Confidence:** Medium-High.

#### SEC-05 — Device tokens are full-power 30-day sessions obtainable without attestation (the "narrower" claim is false)
- **Type:** Defect (auth design / attestation bypass) · **Severity:** Medium · **Evidence:** CODE-VERIFIED
- **Files:** `aura_backend/api/v2/auth.py:238-262`; `services/jwt_auth.py:40-47,169-181,276-308`; `aura_service.py` `_build_authenticator` (no `platform`/`device_id` scoping)
- **Trigger:** Any Google account completes the public desktop PKCE flow. The client id is public at `config.dart:39-42`, and `verify_identity_token` accepts the desktop audience. The account then calls `POST /api/v3/auth/desktop/login`.
- **Actual:** `issue_device_token` mints a 30-day access JWT with the same claims as a session token, plus `platform`/`device_id`. Nothing downstream narrows what a token with those claims can do (grep for `platform` and `device_id` in `aura_core/api`, `aura_service.py` and `services/` finds only the revocation check). Mobile `/auth/token` requires Play Integrity or App Attest, but every account can skip it by using `/desktop/login` and get a *longer-lived* token than the attested path (15 min).
- **Expected:** Per the docstring (`auth.py:251-254`, "narrower, revocable device-scoped token"), device tokens should carry a scope claim that the authenticator enforces (for example, routes limited to desktop needs), or have a shorter TTL plus rotation.
- **Impact:** Device attestation is defense in depth only, but as shipped it is decorative. A stolen device token is valid for 30 days, and revocation does not end live WebSockets (SEC-06).
- **Root cause:** Scope is declared in the docstring and never enforced.
- **Minimal fix:** Add a `scope` claim and map it to allowed route classes in `_build_authenticator`. Alternatively, issue a refreshable 15-minute pair with a device-bound refresh token.
- **Regression test:** A device token on `/api/v3/admin/status` gets 403 even for an Admin email, or is accepted only on an enumerated route set.
- **Confidence:** High on the code. Severity depends on how much the threat model relies on attestation.

#### SEC-06 — Live WebSockets (host agent, voice) outlive token expiry and device revocation
- **Type:** Defect (session revocation) · **Severity:** Medium · **Evidence:** CODE-VERIFIED
- **Files:** `aura_core/api/host_agent.py:61-119`; `aura_core/api/voice_live.py:116-235`; `services/jwt_auth.py:189-220,291-296`
- **Trigger:** A daemon connects with a device token, and the owner then revokes the device (`revoke_device`). Or the access token expires.
- **Actual:** Authentication happens once at connect. `reauth` is optional and client-initiated. No deadline is derived from `exp`, and nothing rechecks `is_device_revoked`. The registered `HostAgentSession` keeps receiving server-routed tool calls (file writes, `run_command`) until the socket drops. A grep for `expir|revok|deadline` across these modules finds only RPC idle timeouts.
- **Expected:** A per-connection deadline equal to the token's `exp`, after which the server requires a reauth frame or closes. Revocation also closes live sessions (a registry lookup by `device_id`).
- **Impact:** "Revoke this device" (the comment calls it "the entire revocation mechanism") does not stop a compromised or lost daemon that is still connected.
- **Minimal fix:** Carry `exp` and `device_id` on `Principal`. In the receive loop, use `asyncio.wait_for` up to `exp`, and close with 4401 if no valid reauth arrives. `revoke_device` should signal the registry.
- **Regression test:** Connect with a token that has `exp=now+2s` on a `FrozenClock` or a short TTL, send no reauth, and assert close code 4401. Revoke the device, and assert the session is unregistered.
- **Confidence:** High.

#### SEC-07 — Untrusted tool output flows verbatim into later tool arguments via `${sN.output}`, and the approval shows unresolved tokens
- **Type:** Defect (prompt-injection amplification, ties to CB-04/CB-05) · **Severity:** Medium · **Evidence:** CODE-VERIFIED
- **Files:** `aura_core/execution/engine.py:60-83,465-489`; `aura_core/planning/engine.py:53-64,320-336`
- **Trigger:** A plan of the form `s1: fetch_url(...)` (or any `untrusted_content` tool), then `s2: run_command(command="${s1.output}")` or `write_file(content="${s1.output}")`.
- **Actual:** `_resolve_output_tokens` substitutes raw step output, which is not fenced and not passed through the safety guard, into the next call's arguments at dispatch time. For `workspace_local` tools the policy then allows the call without confirmation (`policy.py:114`). For other tools the user approves `pending_tool_calls` whose arguments were produced *before* resolution on a different plan (CB-04).
- **Expected:** An argument containing a resolved untrusted output marks the call as tainted, and the call requires confirmation whatever `workspace_local` says. The confirmation shows the resolved arguments, and it is bound to their digest.
- **Minimal fix:** In `_run_step`, if any referenced dependency step's tool has the `untrusted_content` tag, or the context is tainted, pass `confirmed=False` and force `confirm` for side-effecting tools, including `workspace_local` ones routed to hostd.
- **Regression test:** Use the in-memory planner with a scripted plan `fetch_url`, then `run_command("${s1.output}")`, with the desktop registry active. Assert `needs_confirmation`.
- **Confidence:** Medium-High. Whether a model emits such a plan is model-dependent; the mechanism is not.

### OPPORTUNITIES (hardening, not demonstrated defects)

- **SEC-08 — No `email_verified` check before email-based role grants** (`services/security.py:310-352`, `services/rbac.py:78-91`).
  - Roles come from `SUPERADMIN_EMAILS`, `ADMIN_EMAILS` and `DEVELOPER_EMAILS` matched on the token's `email`, and nothing in the backend reads `email_verified` (grep is empty).
  - If the Firebase project has the email/password provider enabled (console configuration, UNVERIFIED), anyone holding the public Firebase API key can register an unverified account with an operator's address and be granted SuperAdmin.
  - Fix: require `email_verified is True` (and `firebase.sign_in_provider == "google.com"`) before any env-list match.
  - Severity if the provider is enabled: Critical. As code alone: Medium.
- **SEC-09 — Socket-proxy allowlist (`CONTAINERS=1`, `POST=1`) still lets a compromised gateway create a privileged container that binds `/`** (`docker-compose.prod.yml:609-629`, dev `:392-412`; parity is OK). The adapter itself is well confined (`docker_sandbox.py:252-292`: `cap_drop ALL`, `no-new-privileges`, uid 10001, `read_only`, `pids_limit`, internal network). Consider an authz plugin, sysbox/gVisor, or a narrow sandbox-spawner sidecar that accepts only the adapter's fixed `HostConfig`.
- **SEC-10 — Supply chain.** `requirements.lock` has zero `--hash` entries and is compiled with `--index-strategy unsafe-best-match` plus `--extra-index-url download.pytorch.org` (lock header lines 1-4). That is the dependency-confusion pattern of the torchtriton incident. Fix: `--generate-hashes` and `pip install --require-hashes`, and restrict the extra index to `torch*` (uv `[[tool.uv.index]] explicit=true`).
- **SEC-11 — Deploy trigger hardening.** `deploy-backend.yml:26-29,50` and `control-plane-deploy.yml:31-34,92-94` gate only on `workflow_run.conclusion == 'success'` with a `branches: [main]` filter. A fork PR whose head branch is named `main` can trigger a production-environment job. Today the VPS fetches only `origin main` and falls back to `vps-update.sh` (`deploy-backend.yml:110-117`), so the impact is a forced redeploy of main. Add `github.event.workflow_run.event == 'push' && github.event.workflow_run.head_repository.full_name == github.repository`.
- **SEC-12 — CORS default in prod includes `http://localhost`, `http://127.0.0.1` and `:8080` with `allow_credentials=True`** (`docker-compose.prod.yml:463`, `aura_service.py` `_apply_cors`). The API is bearer-only, so the impact is low. Drop the localhost origins from the prod default.
- **SEC-13 — `torch.load(..., weights_only=False)`** (`adapters/ml/pytorch_adapter.py:124-127,315`) on operator-supplied checkpoint paths is pickle RCE if an artefact path is ever attacker-influenced. Use `weights_only=True`.
- **SEC-14 — Secret-scan guard scope.** Extend `test_no_committed_client_secrets.py` to `aura_hostd/`, `aura_admin/` and non-Dart client files (plist, json, xml, yaml).
- **SEC-15 — Refresh tokens:** add reuse detection (family revocation on replay of a consumed token). Also replace `revoke_all_for_uid`'s `SCAN` over all refresh keys with a per-uid set.

## 3. Coverage

Read or grepped:
- **Backlog:** CB-03…CB-14.
- **Backend auth:** `aura_service.py` (whole file), `services/jwt_auth.py` (whole file), `services/rbac.py`, `services/security.py` (attestation and identity), `api/v2/auth.py`, `api/admin_platform.py` (role/forget), `api/v2/github_integration.py`, `api/v2/sync.py` (ownership), `infrastructure/http_limits.py`, `infrastructure/db/user_repo.py`.
- **v3 HTTP surface:** `aura_core/api/v3.py` (request models, principal, service-key gating, rate limit, ID-param routes), `api/host_agent.py`, `api/voice_live.py`; an admin-route role-check sweep (heuristic script, no misses).
- **Kernel, tools and execution:** `execution/engine.py` (confirmation and token resolution), `planning/engine.py` (step ids, replan), `kernel/kernel.py` (classification, `_should_plan`), `tools/policy.py`, `tools/builtin.py` (`fetch_url`), `domain/network.py`, `domain/context.py` (fence).
- **Memory, knowledge and hosted inference:** `memory/session.py`, `knowledge/engine.py` (ownership), `llm/sensitivity.py`, `voice/session.py`.
- **Sandbox:** `adapters/docker_sandbox.py` (run kwargs).
- **SQL and deserialization:** a sweep for f-string SQL and unsafe deserialization.
- **hostd:** `command_policy.dart` (whole file), `task_handler.dart` (`run_command`), `control_server.dart` (auth and token file).
- **Deploy:** the nginx template, `conf.d/proxy_headers.inc`, both compose files (secrets, networks, socket proxy, uvicorn flags), the workflow triggers, `ci-deploy.sh`, and the lockfile header and pins.

Not covered:
- `aura_admin/`.
- `api/automation.py` and `api/learning.py` in depth.
- `adapters/host_git.py`.
- The hostd containment implementation (only its tests were glanced at).
- MCP client trust.
- Connector credential storage crypto.
- Live VPS state (no network).
- No tests were executed.

## 4. Cleared hypotheses (tried to falsify; held up)

- **Service key on WebSockets bypassing route scoping.** Cleared. Both WS routes get `person_authenticator` (`v3.py:1411-1426`), pinned by `tests/unit/test_service_keys.py:192-200`. HTTP routes check `service_key.allows` in `principal()` (`v3.py:233-246`) and force `use_tools/use_memory=False` (`v3.py:373-378`).
- **IDOR on documents, memory, workspaces and agent runs.** Cleared. Every path id is owner-scoped in the store call (`v3.py:722-733,1040-1053,1178-1196,1266-1291`). Ingest of a foreign `document_id` is refused (`knowledge/engine.py:269-270`). Sync push refuses rows owned by another user (`api/v2/sync.py:173-176`).
- **Attestation mock tokens accepted in prod.** Cleared. The mock-token check also requires `not environment_is_production_like()` (`services/security.py:119-131`).
- **SQL injection in connector profiling.** Cleared. Identifiers pass through `quote_identifier` (`connectors/mssql.py:448-460`, `mysql.py:48`). The remaining f-strings interpolate constants or `int()` values only.
- **Compose isolation parity.** Cleared for networks (`internal:` flags match) and the socket-proxy env (identical). The divergences that remain are the secret sources (SEC-03) and `aura_edge` sharing (CB-09).
- **Hosted-inference classification bypass.** Cleared. Unscoped generations default to SENSITIVE (`llm/sensitivity.py:50,86-101`), scopes are monotonic, the kernel escalates on memory, documents, CAG, goals and unclean session history (`kernel.py:941-966`), and the tool registry escalates on any tool output (`tools/registry.py:392`).
- **Fence breakout by content.** Cleared for content: `<<<`/`>>>` are neutralized, and the nonce is a per-turn `secrets.token_hex(6)` (`domain/context.py:32-42,208`). Minor: the `source` attribute itself is not escaped, but an attacker cannot close the fence without the nonce.
- **CSWSH / loopback CSRF on the hostd control server.** Cleared. It binds 127.0.0.1 and requires a bearer token (`control_server.dart:117,138-139,227-229`). File permissions on the token are covered in SEC-01.
