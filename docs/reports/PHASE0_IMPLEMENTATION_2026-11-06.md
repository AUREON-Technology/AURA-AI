# Phase 0 implementation report — code items

**Roadmap:** `docs/reports/audit/full-system-audit/remediation_roadmap.md`, Phase 0 "Critical safety and correctness" · **Branch:** `phase0-critical-safety` (from `main` @ `ced341b`) · **Date:** 2026-11-06 · **Status:** code items built and tested locally; **not merged, not deployed, not proven live.** Owner actions (below) are untouched: they need dashboards and credentials this session does not have.

Every code item the roadmap lists is implemented, each in its own commit so it can be reviewed, merged or reverted alone. Per the repository's standing rule, nothing here is "proven": that needs the acceptance evidence named in the roadmap, recorded under `docs/reports/`, after the owner actions land.

## Commits

| Commit | Finding | Change | Tests added |
|---|---|---|---|
| `ff4f29b` | FSA-001 (P1) | `TurnRequest.inherited_taint`, set by `delegate_to_agent` from `ToolContext.tainted`, OR'd into the child turn's taint at both kernel taint sites | `test_delegation.py::TestTaintInheritance` (2), `test_kernel_degradation.py::TestTaintCrossesDelegation` (2, incl. end-to-end tainted orchestrator → finance_advisor `propose_expense` held) |
| `9f6b336` | FSA-017 (P3) | `remember_fact` out of `_READ_TOOLS`; granted explicitly to acting personas, not `decision_agent` | `test_agents.py::TestMemoryWriteGrant` (7) |
| `60525bc` | FSA-031 (P2) | `AuraSettings.validate()` refuses `local-embeddings` / `hash-embed-v1` in staging/production; `/health/ready` gains `embedding {provider, model, dimension, semantic}`; `control-plane.md` lists `AURA_EMBEDDING_*` as required | `test_settings_validation.py::TestEmbedderIsRefusedInProduction` (5), `test_voice_endpoints.py::TestReadinessNamesTheEmbedder` (1) |
| `0e46146` | FSA-007 (P2) | `LiveVoiceConfig.max_frame_bytes` (32 000); `push_pcm` raises `PcmFrameError`, endpoint closes 4400; utterance buffer capped at `max_utterance_ms` worth of bytes | `test_live_voice_session.py` (2), `test_voice_live_endpoint.py` (1) |
| `c575904` | FSA-005 (P2) | legacy `RateLimitMiddleware` no longer wired (aura_core `SlidingWindowRateLimiter` keyed on `extract_client_ip` is the one limiter); Dockerfile CMD gains `--forwarded-allow-ips ${AURA_FORWARDED_ALLOW_IPS:-*}` | `test_deploy_robustness.py::TestOneLimiterBehindATrustedHop` (4) |
| `146290b` | FSA-008 (P2) | backup sidecar mounts `./secrets/backup` only; `bootstrap-secrets.sh` creates it and moves the four backup files; guides updated | `test_backup_secret_scope.py` (4) |
| `73f9554` | FSA-003 (P3) | secret-literal scan intersects the walk with `git ls-files`; audit report's quoted PEM marker allowlisted | existing scan, now green on a checkout with local secrets |
| `f58c6ec` | FSA-006 (P2) | hostd: destructive verbs keep argument containment in trusted roots (per shell segment, incl. `cd ..` first); hard denylist catches `rm -r -f` with separated flags; ADR-011 records the rule | `command_policy_test.dart` (25) |
| `ba17ad4` | FSA-004 (P2) | `/desktop/login` issues a 15-min access token + device-bound refresh token; `/auth/refresh` keeps the binding and refuses a revoked device; `revoke_device` drops the device's refresh tokens; `device_meta:*` (name, kind, created_at); `GET /desktop/devices`; `device_name` through pairing start → approve → poll; client stores the refresh token, sends its hostname, new Settings › Desktop devices page with revoke; daemon sends `Platform.localHostname` | backend 11 (`test_desktop_login.py`, `test_device_pairing.py`), client 5 (`desktop_devices_test.dart`), hostd 1 |
| `8cff324` | FSA-005 / FSA-004 follow-up | legacy `test_security.test_rate_limiting` pins the single limiter instead of driving the unwired middleware; `docs/guides/api-reachability.md` regenerated (the two device routes are now "called") | — |
| `b6905fa` | FSA-007 follow-up | handler logs the oversized-frame close; its endpoint test accepts the test client's plain `websocket.close` message (it had hung the unit suite at 95%) | — |

## Verification (EXECUTED-HERE, Windows 11, temp redirected to `E:`)

| Suite | Result |
|---|---|
| `aura_hostd`: `dart analyze --fatal-infos` | clean |
| `aura_hostd`: `dart test` (full) | **255 passed, 0 failed** (the audit's 10 Windows failures were a full temp volume) |
| `aura_client`: `flutter analyze` | clean |
| `aura_client`: `flutter test` (full) | **543 passed, 3 skipped, 0 failed** |
| `aura_backend`: targeted files per item (listed above) | all green |
| `aura_backend`: legacy `unittest` `test_desktop_login` + `test_device_pairing` | 37/37 |
| `aura_backend`: `pytest tests/security` | **272 passed, 0 failed** |
| `aura_backend`: `pytest tests/architecture` (after regenerating `docs/guides/api-reachability.md`) | **347 passed, 0 failed** |
| `aura_backend`: `pytest tests/e2e tests/integration tests/deployment` | **44 + 33 + 4 passed, 0 failed** |
| `aura_backend`: `pytest tests/unit` (full, 3 635 collected) | **3 573 passed, 56 skipped, 6 failed** — all six also fail in the audit's baseline run on this host and are Windows-only: `test_adaptation_git` goldens (2, CRLF byte counts), `test_host_git` askpass and `test_sandbox_exec_surface[dev]` (cp1252 decoding of UTF-8 text), `test_workspace_manager` admission (2, host-memory probe). None touches code changed on this branch. |
| `aura_backend`: legacy `unittest discover` (full) | **143 run, 37 skipped, 0 failed** after `8cff324` rewrote `test_security.test_rate_limiting` against the one real limiter |

Still not runnable here, as in the audit: `tests/security/test_route_auth.py` (FastAPI version drift in the local venv), load/certification/training suites, Playwright, and every live check.

## Deploy prerequisites — read before merging to `main`

`main` auto-deploys to Railway with no CI gate (FSA-020/021/022). Two of these commits change what a boot or a backup cycle needs:

1. **FSA-031 will refuse to boot Railway unless the embedder is configured.** Before merge, set on the Railway service: `AURA_EMBEDDING_PROVIDER=sentence-transformers`, `AURA_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5`, `AURA_EMBEDDING_DIMENSION=384` (the image ships `sentence-transformers`; `control-plane.md` §3 has the block). If the live service has been indexing with the hash embedder, existing vectors are meaningless and the knowledge index needs re-indexing after the switch. After deploy, `/health/ready` must show `"embedding": {"semantic": true}`.
2. **FSA-008 moves the backup secrets on the VPS.** Run `deploy/scripts/bootstrap-secrets.sh` (it creates `deploy/secrets/backup/` and moves `backup_age_recipients.txt`, `backup_s3_access_key`, `backup_s3_secret_key`, `rclone.conf`) before `docker compose up -d backup`, then run one cycle: that cycle is GAP-046's exit evidence. The VPS deploy path is currently dead (FSA-023), so this waits on that decision.
3. **FSA-005** changes only the image CMD (compose files already passed the flag). On Railway, confirm after deploy that two source addresses get independent rate-limit buckets and that audit rows carry client IPs rather than the edge's.
4. **FSA-004** changes the desktop login response. Older desktop builds keep working (the token manager tolerates a refresh-less reply and a stored 30-day token stays valid until it expires); a new build is needed for the device list and for the refresh-based session. No migration: device metadata is Redis state with the token TTL.

## Owner actions (Phase 0, no code) — unchanged from the roadmap

1. Restore GitHub Actions runners; re-run every workflow on `main` (FSA-021).
2. Protect `main` with required checks; disable Railway auto-deploy or enable "Wait for CI"; set `vars.CONTROL_PLANE_DEPLOY_REQUIRED=true` (FSA-020).
3. Link `aura_backend/railway.json` as the service config; snapshot the dashboard into `docs/reports/` (FSA-022).
4. Confirm the VPS host-key fingerprint out of band; keep (re-pin by PR) or decommission (rotate every shared secret) (FSA-023).
5. Rotate the historical GCP service-account key and every value of the historical `.env`; delete local `deploy/.env.bak-*` and `credentials.json`; record the rotation date (FSA-002, FSA-003).
6. Restore `hfspace` on an always-on flavor or provision another sensitive-capable node; do not raise `hosted1` to `sensitive` without an ADR-030 §4 decision (FSA-024).
7. Enumerate Railway services; create `worker-realtime`, `worker-ingestion`, `beat` if absent; set `AURA_WORKERS=1` until FSA-026's long fix (FSA-027).
8. Set `AURA_METRICS_TOKEN` and confirm `/metrics` → 401 (FSA-009); seal `AURA_DATA_ENCRYPTION_KEYS` offline (FSA-030); confirm `/health/ready` shows `schema.revision 0019`, then `scripts/move_personal_os_records.py --dry-run` (FSA-032).
9. New, from item 1 above: set the three `AURA_EMBEDDING_*` variables on Railway **before** merging this branch.

## Left open inside Phase 0

- **FSA-006, second half:** with several granted roots and no `cwd` in the request, `run` still executes in the first root. The backend has no notion of the IDE's active root to send; that needs a client → `TurnRequest` → `ToolContext.metadata` → `SandboxRunner.run(cwd=…)` thread through the port, recorded in ADR-011 as open.
- **FSA-003, build-script half:** the desktop build scripts already read no client secret (the finding's premise was stale), so only the test scoping was needed; the local `deploy/.env` / `credentials.json` deletion is the owner's.
- **FSA-002, FSA-020–024, 026, 027, 030, 032:** owner actions, above.

## Acceptance criteria status (roadmap wording)

| Criterion | Status |
|---|---|
| green run of every workflow on `main` | not possible until runners return (owner) |
| PR shown blocked by a required check | owner |
| Railway deploy whose trigger is the workflow | owner |
| rotation record committed | owner |
| `tests/unit/test_delegation.py` taint case green | **done** (`ff4f29b`) |
| live RAG turn and live tool turn succeed, recorded | needs FSA-024 (owner) then a live check |
| `/health/ready` reports sensitive-capable provider and embedder | embedder block **done**; sensitive provider is FSA-024 (owner) |
| `/metrics` returns 401 without the token | owner (`AURA_METRICS_TOKEN`) |
