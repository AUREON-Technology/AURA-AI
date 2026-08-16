# AURA AI — Hardware Capacity Report

Measured on `srv1794216`, 2026-08-16.

This report was outstanding through the whole self-hosting mission for one reason: I did not have
the machine's specifications, and writing capacity figures without the hardware they describe would
have been fabrication. The numbers below are now real. Where a figure is still inferred rather than
measured, it says so.

**The headline: this host cannot run the platform's inference tier. Not "slowly" — at all.**

---

## 1. The host, as measured

| Resource | Measured | Source |
| --- | --- | --- |
| CPU | **1 vCPU** on an AMD EPYC 9354P (32-core host part) | `nproc`, `lscpu` |
| Virtualisation | KVM guest (`pc-i440fx-11.0 @ 2.0GHz`) | `lscpu` BIOS model |
| RAM | **3.8 GiB total**, 1.5 GiB used, **2.3 GiB available** | `free -h` |
| Swap | **0 B** | `free -h` |
| Disk | 48 GB total, 20 GB used, **28 GB available** (41 % used) | `df -h /` |
| Public IPv4 | 152.239.118.239 | `ip addr` |

Two of those deserve emphasis. **One vCPU** is a separate constraint from memory and binds
independently. **Zero swap** means memory exhaustion is an OOM kill, not a slowdown — there is no
soft failure mode on this box.

### The host is shared

`srv1794216` is not a dedicated AURA machine:

| Owner | What is running | Bound to |
| --- | --- | --- |
| **VELVET** | Django/gunicorn app + Postgres, host nginx | `0.0.0.0:80`, `:443`, `:8080`; `127.0.0.1:5432` |
| **AURA** | `aura_backend` (net=host), `aura_redis`, 2 Celery containers | `127.0.0.1:6379`; backend port not observed |

Consequences that shape everything below:

- **Ports 80 and 443 are taken** by a host-level nginx serving Velvet. `docker-compose.prod.yml`
  publishes exactly those two ports and nothing else, so it cannot start here without either
  displacing Velvet or being reverse-proxied behind the existing nginx.
- **1.5 GiB of the 3.8 GiB is already spoken for.** The 2.3 GiB "available" figure is the real
  budget, and it is shared with a Django app that will grow under load.
- The service on `127.0.0.1:8000` answering `400 Bad Request` with `Server: gunicorn` is **Velvet's
  Django**, not AURA — that is Django's `ALLOWED_HOSTS` rejection page. AURA's backend runs with
  `network_mode: host` and was not observed on any port I checked.

---

## 2. What the platform actually needs

Summed from the resource limits declared in `deploy/docker-compose.prod.yml`, plus the model:

| Tier | Component | Declared limit | Note |
| --- | --- | --- | --- |
| App | gateway | 3.0 G / 2.0 CPU | 4 uvicorn workers, ~250 MB each |
| App | worker-ingestion | 2.0 G / 1.5 CPU | embedding batches |
| App | worker-realtime | 1.0 G / 1.0 CPU | |
| App | beat, nginx | 0.5 G combined | |
| **App subtotal** | | **6.5 G / ~5 CPU** | **before any model** |
| Inference | Ollama + 7B Q4 | ~6 G | weights ~4.7 GB + KV cache + runtime |
| Data | Postgres | ~1 G | `shared_buffers` ≈ 25 % |
| Data | Qdrant | ~1 G | ~4 KB per 384-dim chunk + HNSW |
| Data | Redis | ~0.25 G | bounded, `allkeys-lru` |
| Observability | Prometheus, Grafana, Loki, Promtail, Alertmanager, node-exporter | ~1.5 G | 30-day retention |
| **Total** | | **≈ 16 GB / 4+ vCPU** | matches the target in the Deployment Guide |

The app tier's declared limits alone — **6.5 GB** — exceed this host's *total* physical memory by
70 %. That comparison is the entire finding, and no amount of tuning closes a gap of that shape.

### Model memory, specifically

| Model | Weights (Q4_K_M) | Resident with 4k context | Fits in 2.3 GiB? |
| --- | --- | --- | --- |
| qwen2.5:7b-instruct | ~4.7 GB | ~5.5–6 GB | **No** — 2.5× over |
| qwen2.5:3b-instruct | ~2.0 GB | ~2.5 GB | **No** — over budget, and nothing left for the app |
| qwen2.5:1.5b-instruct | ~1.0 GB | ~1.3 GB | Barely, leaving ~1 GB for everything else |
| qwen2.5:0.5b-instruct | ~0.4 GB | ~0.6 GB | Yes, and it is not worth serving |

There is no row in that table that is both viable and useful.

### The embedding tier has the same problem, less obviously

`AURA_EMBEDDING_PROVIDER=sentence-transformers` with `BAAI/bge-small-en-v1.5` is the compose
default. The model itself is small (~130 MB), but sentence-transformers pulls in **torch**, whose
resident footprint is roughly **0.8–1.5 GB** once loaded — per worker process. On a 2.3 GiB budget
that is the difference between running and not.

The `hash-embed-v1` default in `settings.py` needs no model and no torch. It is deterministic and
fast, and its retrieval quality is materially worse than a real embedding model. It is the right
choice for tests and the wrong choice for a corpus you intend to search.

---

## 3. Throughput, estimated

**These are estimates, not measurements.** No model has been run on this host. CPU generation for a
quantised model is memory-bandwidth-bound rather than compute-bound, so these are derived from
model size and typical single-core bandwidth on KVM guests, and should be treated as an order of
magnitude. Measure before relying on any of them.

| Model | Est. tokens/sec, 1 vCPU | A 200-token reply takes |
| --- | --- | --- |
| 7B Q4 | 1–3 | 1–3 minutes *(cannot load anyway)* |
| 3B Q4 | 3–6 | 35–70 s |
| 1.5B Q4 | 8–14 | 15–25 s |

Two thresholds are worth separating, because I conflated them in an earlier draft of this report.
`AuraSlowTimeToFirstToken` in `deploy/monitoring/alerts/aura.yml` fires at **p95 TTFT > 8 s**; the
*target* of 3 s appears in the Performance Report's operating table. The alert is the looser of the
two by design — an alert that fires on the target fires constantly.

Time-to-first-token is dominated by prompt processing, not generation rate, and a RAG turn arrives
with a couple of thousand tokens of retrieved context. On one core that is *estimated* at tens of
seconds for a 7B and high single digits for a 1.5B — so even the smallest viable model is marginal
against the 8 s alert and misses the 3 s target. Both figures are inferred, and the honest summary
is that this host is not close enough for the distinction between the two thresholds to matter.

`OLLAMA_NUM_PARALLEL: "2"` in the compose file assumes two concurrent generations. On one core they
share it, so concurrency here halves the per-request rate rather than adding throughput.

---

## 4. Disk

28 GB available is the one resource that is not critical.

| Item | Size |
| --- | --- |
| 7B Q4 weights | ~4.7 GB |
| bge-small + tokenizer cache | ~0.2 GB |
| Docker images (full stack) | ~4–6 GB |
| Prometheus, 30-day retention | ~5 GB |
| Postgres + Qdrant, early corpus | ~2 GB |
| **Total** | **~16–18 GB** |

It fits in 28 GB, with the caveat that Prometheus retention is the growth term and the default is
worth shortening to 14 days on a 48 GB disk. Disk is not what blocks this deployment.

---

## 5. Verdict

**This host cannot run the AURA stack as designed.** It is roughly one quarter of the documented
target on both axes that matter:

| | This host | Documented target | Ratio |
| --- | --- | --- | --- |
| vCPU | 1 | 4 | **0.25×** |
| RAM | 3.8 GB | 16 GB | **0.24×** |
| Swap | 0 | — | no soft failure |

And it is shared with another production application that already owns ports 80, 443 and 8080.

There is a temptation to describe a reduced configuration that "works" here — hash embeddings, a
1.5B model, no observability tier. I do not recommend it and will not present it as a deployment
option, because it would produce a system that answers slowly, retrieves badly, and reports nothing
about why. That is worse than an honest "not yet", because it looks like it is working.

### Recommended targets

| Goal | Spec | What it gets you |
| --- | --- | --- |
| **Minimum for the documented stack** | 4 vCPU / 16 GB / 100 GB | 7B Q4 on CPU, full observability. TTFT will still be seconds, not sub-second. |
| **Comfortable** | 8 vCPU / 32 GB / 200 GB | Room for a larger model, real concurrency, and Velvet on the same box. |
| **If generation quality matters** | above + 1 GPU, 24 GB VRAM | Roughly 10–20× the tokens/second (Deployment Guide §Sizing). This is the upgrade that changes the product, not just the metrics. |

**The cheapest path that actually works** is to split the tiers rather than buy one large machine:
keep the gateway, Postgres, Qdrant and Redis on a modest host, and point
`AURA_PROVIDER_LOCAL_BASE_URL` at a separate inference box. That is a configuration change, not a
code change — which is the payoff for declaring providers as data (ADR-003), and it means the
inference host can be rented by the hour for evaluation before anything is committed to.

### What this does not change

Nothing here is a defect in the platform, and nothing here is fixable in software. The application
tier's memory budget is dominated by a language model, and a 7B model needs about 6 GB to exist.
This is a procurement decision.

---

Related: [Deployment Guide](docs/guides/deployment-guide.md) ·
[Provisioning Runbook](docs/guides/provisioning-runbook.md) ·
[Production Readiness Score](AURA_AI_PRODUCTION_READINESS_SCORE.md) ·
[Scalability Report](docs/analysis/07-scalability-report.md)
