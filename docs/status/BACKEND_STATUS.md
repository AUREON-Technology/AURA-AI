# Backend status

**As of 2026-10-28, `main` at `dc87beb`.** Covers `aura_backend/` and the deploy stack under
`deploy/`. Evidence scale and refresh steps: [README.md](README.md).

## Summary

The backend is well architected, and Backend CI is green on `main`:
- pytest: 3848 passed, 56 skipped;
- legacy `unittest`: 100 OK;
- `pip-audit`: clean;
- `docker-build`: passes.

**Production today is the Railway control plane**, running `main` (`dc87beb`), with:
- `hosted1`: healthy;
- `hfspace`: 503;
- no Ollama.

The Contabo VPS has not taken an automated deploy in about six weeks. Its SSH host key changed,
and the deploy refuses to connect.

Under the repository's evidence rule, almost nothing is **proven**. The only `proven` gap is
GAP-043 (CI runners restored). Live runs of v3 code exist only on Railway (`flame-link2-live.md`)
and in-process (the OpenRouter Phase 6 run). The 2026-10-26 audit raised 38 findings, 18 High. Two
are fixed since then and two partly.

## Where it runs

| Target | State (checked 2026-10-28) |
| --- | --- |
| **Railway control plane** (`aureon-ai.tech`) | Serves `dc87beb` (`server: railway-hikari`, Railway deployment ID); `environment: production`; schema `0013`; blueprint v2 active. Providers: `hosted1` healthy, `hfspace` `HTTP 503`. Deploy Control Plane (Railway) succeeds on every `main` merge. |
| **Hugging Face GPU Space** (`hfspace`) | Down or asleep (503 from `/health/ready`) |
| **Contabo VPS** (169.58.188.43, `/opt/aura-stack`) | Stale and unreachable for CI. Deploy VPS has failed on every run since at least 2026-09-27. Since 2026-10-18 the failure is `Host key verification failed`; the new key is ED25519 `SHA256:6dFAzu5+m0A5GSFG5QRt/rNSyZ2JleERZNeRoBWLk2g`. What it runs now is unknown. |
| **Dev stack** (`dev.aureon-ai.tech`) | DNS no longer resolves |

## Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Backend CI gate (test, pip-audit, docker-build) | PROVEN | GAP-043; Backend CI run 40719101969 green on `dc87beb` |
| Hexagonal layering fitness tests | TESTED | `tests/architecture/`; F-28: narrower than CLAUDE.md claims (misses absolute and vendor-SDK imports) |
| Self-hosting invariant / ADR-030 host categories | TESTED | `tests/architecture/test_no_external_inference.py` |
| AIKernel plan → execute → generate | TESTED (scripted/echo providers) | GAP-021 open; F-24: planner failure silently becomes "answered directly" |
| Tool confirmation / taint (ADR-006) | TESTED, defect open | F-04: confirmation matched by name and position, not bound to the approved call; GAP-014 |
| ProviderRouter, breakers, failover | TESTED, defect open | F-19 (confirmed by repro): a cancelled half-open probe locks the provider until restart (`llm/circuit.py`) |
| Self-hosted LLM (qwen2.5:3b, CPU Ollama) | TESTED in a sandbox only | A4 sandbox p50 56 s / p95 71 s (`docs/reports/phase-a4-load-test.md`); not on the current production target |
| Hosted open-weight inference, ADR-030 Phases 1–5 | TESTED | GAP-064; inert unless `AURA_ALLOW_HOSTED_INFERENCE=true` |
| ADR-030 Phase 6, live | BUILT (`built_not_proven`) | In-process run against live OpenRouter (`nvidia/nemotron-3-ultra-550b-a55b:free`, `ALLOW_PROMPT_TRAINING=true`): 9/10 checks pass, the 429 burst was skipped (`docs/reports/phase-6-hosted-inference-openrouter-inprocess-20261028.txt`). The Verify Hosted Inference workflow has never run. |
| Hybrid retrieval / knowledge | TESTED (hash embedder) | A3 recall@5 0.65; F-14 (confirmed): with several workers, deleted docs stay searchable through BM25 |
| Memory engine | TESTED | F-15 fixed (`19244e6`); F-16 write races open |
| v2 sync protocol | TESTED, defects open | F-11 (no per-row SAVEPOINT), F-12 (cursor taken from the app clock), F-13 |
| Auth / RBAC / JWT | TESTED, partly fixed | F-02 refresh path fixed (`2d42388`), demotion is still not enforced; F-03 and F-10 open |
| Security regression suite (P1.5) | TESTED | GAP-048 `built_not_proven` |
| Engineer workspaces (ADR-013/014) | BUILT | GAP-025; off by default; no `verify-engineer-workspaces` report |
| Desktop hostd bridge | BUILT | GAP-026; F-21 (confirmed): non-streaming `run` returns `''` / `-1` |
| Observability | OPEN | GAP-036, GAP-037 |
| Backup / DR | TESTED in a sandbox; OPEN on any live target | A2 sandbox (`phase-a2-restore-drill.md`); GAP-028, GAP-046 |
| 50-user capacity | OPEN | GAP-011 (P0), GAP-041 |
| Real-model evaluation gates | OPEN | GAP-021; GAP-006 (`eval-ci.yml` is dispatch-only) |
| Schema / migrations | TESTED, risk open | Alembic `0001`→`0013` on `main` (`0014` on PR #185), single head; F-27: 16 runtime-DDL tables exist in no migration |
| Learning / fine-tuning | BUILT | GAP-017; GAP-018 (a failed train is recorded as success) |
| Voice | BUILT | GAP-020: speech-to-text is English-only while an Arabic voice ships |
| Track AP: AP1–AP8 | TESTED (`built_not_proven`) | GAP-049…057; held at BUILT until M0 |
| Track AP: AP10 | In progress | GAP-059 |
| Track AP: AP9, AP11–AP13 | OPEN | GAP-058, GAP-060…062 |
| Flame integration AR-1…AR-8 | TESTED; one live Railway run | GAP-063; Link 2 passed only under a gate-coverage waiver (0.35); Link 1 never run |

## Open issues (most severe first)

**Operational**
1. **The VPS deploy path is dead** (host-key change; CB-03). The owner must confirm whether the
   server was reinstalled before anyone updates `deploy/known_hosts`. Also decide whether the VPS
   is still a target at all, since the domain has moved to Railway.
2. **`main` is unprotected** (GAP-005 / CB-02).
3. **`hfspace` is down (503).** The production model capacity is whatever `hosted1` provides.

**Fixed since the 2026-10-26 audit**
- F-15 (`19244e6`): memory decay no longer compounds.
- F-05 (`5153394`, client side): sign-out revokes the refresh token and wipes local data.
- F-01, partly (`627a03d`): the hostd untrusted tier no longer runs repo code. The server-side
  `workspace_local` taint exemption is still open (`tools/policy.py:114-115`, CB-05).
- F-02, partly (`2d42388`): role is re-resolved on refresh. `db_role` is never populated and
  demotion revokes no sessions.

**Fixed on PR #185 (TESTED locally; not merged, CI not yet run)**
- **F-03:** dev stack gets its own `dev_jwt_secret` and `dev_data_encryption_keys`; tokens carry
  `iss`/`aud`, with legacy tokens accepted until `AURA_JWT_LEGACY_TOKENS_UNTIL` (2026-12-15).
- **F-04:** confirmation is by single-use `approval_id` bound to the stored turn and call digest
  (`aura_core/execution/confirmations.py`); a stale or reused id is a 409.
- **F-19:** `CircuitBreaker.release_probe()` frees the half-open slot on cancel, 429 or refusal.
- **F-14:** lexical hits for inactive documents are dropped.
- **F-11, F-12:** per-row SAVEPOINT on push; the pull cursor is `pg_current_xact_id()`
  (migration `0014`, Postgres-verified in `tests/load/test_sync_postgres.py`).
- **F-07 (sync half):** `DELETE /api/v3/sessions/{id}`; deletes clear `*_enc` server side.

**Still open (High)**
- **F-21:** hostd non-streaming `run` returns nothing.
- **F-20:** Celery reuses one asyncpg pool across event loops.
- **F-06:** admin erasure reports success while incomplete.
- **F-22:** timeouts disagree between layers.
- **F-27:** 16 tables have no migration.
- **GAP-011, GAP-041 (P0):** inference is single-slot and no load matrix exists.

**Medium:** F-08 (GitHub OAuth CSRF), F-09, F-10 (falls back to a random per-process JWT secret),
F-13, F-16–F-18, F-23–F-26, F-28, F-30, F-33–F-38.

**Conditional:** SEC-08 / F-38 becomes Critical if Firebase email/password sign-in is enabled.
That needs a check in the Firebase console.

## Changed since 2026-10-20

- `e7e0db1`: full-system audit at `699f27b`, with a remediation roadmap.
- `3090b8f`: OpenRouter free models behind the per-slot `ALLOW_PROMPT_TRAINING` consent; a guard
  against reasoning text leaking into answers; live wire captures; the in-process Phase 6 run.
- `a70579a`: seven dependency bumps for new advisories. pillow 13 and filelock 4 are major bumps.
- `627a03d`, `2d42388`, `19244e6`: the audit fixes listed above.
- Open PR #185: an `openrouter-free` preset for the Verify Hosted Inference workflow, so it needs
  only the `AURA_HOSTED1_API_KEY` secret.

## Next steps

1. **Owner:** establish why the VPS host key changed, and decide the VPS's future now that the
   domain is on Railway. If it stays, update `known_hosts` and deploy `main`.
2. Enable branch protection with required checks (GAP-005).
3. Finish the R0 security items:
   - make the `workspace_local` exemption routing-aware (F-01b);
   - complete F-02;
   - separate dev secrets and add `iss`/`aud` (F-03), changing both compose files in one commit;
   - refuse to start without a JWT secret (F-10).
4. Bind confirmations to the approved call (F-04).
5. Fix the data-loss and lockout defects: F-14, F-19, F-20, F-11, F-21.
6. **ADR-030 Phase 6:**
   1. The owner adds the `AURA_HOSTED1_API_KEY` secret.
   2. Merge #185.
   3. Run Verify Hosted Inference with check 10.

   Its VPS step needs the VPS back. Against Railway, the in-process report is the current
   evidence.
7. Fix the F-22 timeout budgets and add metrics (GAP-036), then run the M5 load matrix (GAP-041).

## Unverified

- What the VPS runs, and whether its host-key change was legitimate.
- Which provider and model `hosted1` is on Railway (the health endpoint does not say).
- The "5–10 tok/s" figure for qwen2.5:3b. No measurement on a live target is committed.
- F-20 at runtime (needs worker logs). F-03 secret paths on the VPS. The Firebase providers
  (SEC-08).
- The status table of the master roadmap (`AURA_MASTER_UPGRADE_ROADMAP.md:501-517`) is stale. It
  still says M0 "Not started" although GAP-043 is proven.
