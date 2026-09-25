# Deployment Topology

The current production topology as it exists in `deploy/`, and the target topology the
[production roadmap](MASTER_PRODUCTION_ROADMAP.md) moves toward (Phases 2, 3, 19, 20). Verified
against tree `11abb50` on 2026-09-24. Host sizing figures come from comments in the compose files
and `deploy/.env.example`. They were not measured in this pass.

## 1. Current topology (as built)

```
                     Internet
                        │  :80 / :443  (HTTP/1.1 only, no `http2 on;`, PR #65)
               ┌────────▼─────────┐
 single VPS    │      nginx       │  the only container with a published port (SEC-12)
 /opt/aura-    └───┬──────────┬───┘
 stack             │ edge     │ aura_edge (shared with the aura-dev project)
 (Contabo,     ┌───▼────┐ ┌───▼─────┐
 CPU-only,     │gateway │ │ console │  (Next.js admin)
 "8 cores,     └───┬────┘ └─────────┘
 no NVIDIA")       │ data (internal: true)
      ┌──────────┬─┴────────┬───────────┬──────────────┬─────────────┐
   postgres    redis      qdrant   worker-realtime  worker-ingestion  beat
      │                                    │               │
      └──── backup (data + egress) ────────┘      ollama (data, egress, aura_edge)
                                                  qwen2.5:3b-instruct-q4_K_M, CPU,
                                                  OLLAMA_NUM_PARALLEL=1
 observability: prometheus, alertmanager, grafana, loki, promtail, node-exporter
 opt-in profiles: sandbox (docker-socket-proxy, sandbox-image, sandbox-proxy),
                  training (worker-training), gpu (vllm)
```

| Fact | Evidence |
| --- | --- |
| One VPS, checkout at `/opt/aura-stack` | CLAUDE.md. `deploy-backend.yml:99-101` (`AURA_REPO=/opt/aura-stack`) |
| Two Compose projects on the same host: `aura` (prod, `deploy/docker-compose.prod.yml`, `name: aura` at line 18) and `aura-dev` (`deploy/docker-compose.dev.yml`) | CLAUDE.md. `vps-update.sh` touches only `aura` |
| 17 services running in prod | Roadmap Phase A1 (verified live 2026-08-23) |
| nginx is the only published port (`80:80`, `443:443`) | `docker-compose.prod.yml:141-147`. The dev compose has no `ports:` |
| Data tier on `internal: true` networks | `docker-compose.prod.yml:35-37` (`data`), sandbox networks `:55-72` |
| Inference: Ollama on CPU, one resident model, one generation in flight | `docker-compose.prod.yml:665-697` (`OLLAMA_MAX_LOADED_MODELS "1"`, `OLLAMA_NUM_PARALLEL "1"`, limits 6 CPU / 10 G) |
| The dev stack uses the prod Ollama over `aura_edge` | `docker-compose.dev.yml:4-5,142`. `docker-compose.prod.yml:48-51` → **GAP-027** |
| Gateway: 4 uvicorn workers, file-backed secrets | `docker-compose.prod.yml:231` (`--workers ${AURA_WORKERS:-4}`), `:294-300` |
| Deploy: merge to `main` → Backend CI → `Deploy VPS` (`workflow_run`) → SSH → `ci-deploy.sh` / `vps-update.sh` | `.github/workflows/deploy-backend.yml:25-113`. **Currently inert: Actions jobs aren't running (GAP-043)** |
| Engineer sandbox, GPU vLLM and training worker are off by default | compose `profiles: [sandbox]`, `[gpu]`, `[training]`. `AURA_SANDBOX_ENABLED` defaults to false |
| Remote HF Space failover is supported but off | `docker-compose.prod.yml:271-287`. It needs `AURA_ALLOW_REMOTE_INFERENCE=true` → **GAP-034** |

**Measured capacity of this topology:** a p50 of 56.3 s and a p95 of 70.5 s per turn, with
throughput flat at 0.04–0.06 turns/s from 1 to 8 concurrent users. This was measured on a 4-core
sandbox configured like production, not on the VPS itself
([phase-a4-load-test.md](../reports/phase-a4-load-test.md)). In practice the platform serves one
AI request at a time.

## 2. Target topology

Principle: **evolve, don't rewrite.** The router (`aura_core/llm/router.py`) already takes any
number of `openai_compatible` providers, declared as data. The target topology is mostly a change
of *hosts and providers*, not of code. There's **no Kubernetes** until a measured need exists
(roadmap §4 scope decisions, audit §40 Q15).

```
                    Internet
                       │ 443
            ┌──────────▼───────────┐        private link (WireGuard / provider VLAN),
            │  CONTROL-PLANE VPS   │        mTLS or API key per pool; no public port
            │ nginx, gateway,      │◄──────────────────────────────┐
            │ console, workers,    │                               │
            │ beat, postgres,      │                    ┌──────────┴───────────────┐
            │ redis, qdrant,       │                    │  GPU INFERENCE HOST(S)   │
            │ observability, backup│                    │  vLLM pools (digest-     │
            │ (+ CPU Ollama kept   │                    │  pinned image, revision- │
            │  as last-resort      │                    │  pinned weights):        │
            │  local provider)     │                    │   • chat / planning      │
            └──────────┬───────────┘                    │   • coder (engineer)     │
                       │                                │   • vision (VLM)         │
            offsite encrypted backups                   │   • embed + rerank       │
            (S3-compatible, rclone crypt)               │  ASR/TTS service         │
                                                        │  node-exporter + DCGM    │
                                                        └──────────────────────────┘
 aura-dev: moved off the prod host, or given its own inference quota (GAP-027)
```

| Tier | Target | Why | Gate before adopting |
| --- | --- | --- | --- |
| Control plane | Stays on one VPS (the current one, possibly resized). Postgres, Redis, Qdrant and the gateway stay co-located | The bottleneck is model serving, not orchestration or data (audit §28). Postgres/Qdrant/Redis haven't been shown to saturate | The Phase 3 load matrix shows the control plane is not the bottleneck at 50 users |
| Inference | Dedicated GPU host(s) running **vLLM**, one pool per workload, each registered as an `openai_compatible` provider with its own `quality_tier`/`max_concurrency` | vLLM's continuous batching removes the `OLLAMA_NUM_PARALLEL=1` ceiling (GAP-011/012) | A pinned model passes the MODEL_REGISTRY admission rule. `tests/load/test_multi_model_routing.py`-style proof against the real pools |
| Fallback | CPU Ollama stays as `local_first` last resort (degraded but sovereign). **No hosted-AI or rented-Space fallback** in production (GAP-034) | Keeps "functional without third parties" | Router failover proven against the pool going down |
| Network | Inference hosts expose no public port, only a private link from the control plane | Keeps the SEC-12 single-ingress property | Port scan from the internet shows only 443 on the control plane |
| Dev | Separate host, or a dedicated small pool. It never shares the production generation slot | GAP-027 | Isolation fitness test |
| Scale-out | Add GPU hosts and pools horizontally behind the router. A second gateway replica only when the load matrix shows gateway CPU saturation | Measured-need rule | Phase 3/19 evidence |
| HA/DR | Documented rebuild of the control plane from offsite backups onto a fresh host. A warm standby only if the RTO target can't be met by rebuild | [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) | A drill report with measured RTO |

### What stays exactly as is

- nginx remains the only public ingress, HTTP/1.1 only on HTTPS server blocks (the CLAUDE.md
  `http2` rule).
- `docker exec` stays forbidden for sandboxes, `EXEC: "0"` and `IMAGES: "0"` stay on the socket
  proxy in both compose files, and the sandbox image stays built, never pulled.
- `container.py` remains the single wiring point. New pools are configuration
  (`AURA_LLM_PROVIDERS`, `AURA_PROVIDER_<NAME>_*`), not new adapter kinds. Only `echo`,
  `openai_compatible` and `ollama` exist, and `tests/architecture/test_no_external_inference.py`
  keeps it that way.
- No model execution on the Flutter client (fitness-tested on `pubspec.yaml`).

### Existing guides this builds on

- [`docs/guides/gpu-hybrid-setup.md`](../guides/gpu-hybrid-setup.md): adding a co-located vLLM
  node as a `large` provider with `quality_first`. This is the closest existing recipe to the
  target inference tier.
- [`docs/guides/split-inference-deployment.md`](../guides/split-inference-deployment.md): the HF
  Space variants. §8's "Space as failover" is **not** part of the target production topology,
  because it sends prompts off-host. Its note that the circuit breaker is global (GAP-035)
  applies to multi-pool vLLM too.
- [`docs/guides/host-migration-runbook.md`](../guides/host-migration-runbook.md) and
  [`provisioning-runbook.md`](../guides/provisioning-runbook.md) cover control-plane rebuilds.
