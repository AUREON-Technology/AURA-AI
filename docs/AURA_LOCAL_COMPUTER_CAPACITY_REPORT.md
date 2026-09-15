# AURA AI — Local Computer Full-System Capacity & Feasibility Audit

**Audit date:** 2026-09-16
**Machine:** HP ZBook 15 G6 (hostname `desktop-9ojj2qc`), Windows 11 Pro
**Raw data:** [`docs/AURA_LOCAL_CAPACITY_RAW.json`](AURA_LOCAL_CAPACITY_RAW.json)
**Evidence key:** every load-bearing claim below is tagged **MEASURED** (read directly off this machine this session), **CONFIGURED** (read from the repo's own compose/env files), **REFERENCE** (a real measurement from a different but comparable environment — Phase A4's load test), **CALCULATED** (arithmetic on the above), **ESTIMATED** (a reasonable figure where direct measurement wasn't possible, usually a public spec sheet), or **UNVERIFIED** (could not be established this session).

---

## 1. Executive Summary

This machine — a 2019 HP ZBook 15 G6 with a 6-core/12-thread Intel i7-9850H, 31.8 GB RAM, an NVIDIA Quadro T1000 (4 GB VRAM), and mixed NVMe/HDD storage — is architecturally capable of running AURA's local-inference stack (CPU-only Ollama, Postgres, Qdrant, Redis, FastAPI gateway), which is exactly the class of hardware the stack's own configuration targets (`deploy/.env.example` describes the reference deployment as an "8 cores, no NVIDIA" host). **It cannot do so safely right now**, for a reason that has nothing to do with CPU/RAM/GPU capacity: the system drive (C:) has only **3.73 GiB free**, and Docker Desktop's own WSL virtual disk already occupies 26.6 GiB there. Pulling or building the stack's container images would risk exhausting the drive, which this audit's own safety rules prohibit — so no live Docker container was started, no image was pulled, and no model was downloaded during this audit.

Beyond that blocking precondition, two further constraints matter once it's cleared:

- **RAM is tight.** The core production compose stack's own configured limits sum to **~25.1 GB**, against 31.8 GB total. At the moment of this audit, with zero AURA services running, this machine already had only **4.7 GB available** (76.8% CPU, ~27 GB RAM in use) — driven by normal desktop use (Cursor, Chrome, Claude Code, Flutter/Dart dev processes). The full stack does not comfortably coexist with a typical active development session on this machine as configured.
- **AI throughput is inherently single-flight**, by configuration, not hardware. `OLLAMA_NUM_PARALLEL=1` is set in both compose files. A real load test on comparable CPU-only hardware (Phase A4, `docs/reports/phase-a4-load-test.md`) measured this directly: **flat throughput (~0.04–0.06 turns/s) from 1 to 8 concurrent requests**, with per-turn latency around 28–56 s median and up to 70.5 s p95. This machine has more cores and RAM than that test's 4-core/15 GB sandbox, so per-turn latency would plausibly be similar or somewhat better — but the one-request-at-a-time ceiling would reproduce exactly, because it's a semaphore, not a hardware limit.

**Verdict: FEASIBLE WITH LIMITATIONS**, gated on a storage fix first. See §25 for the full reasoning.

---

## 2. Computer Hardware Inventory

| Component | Value | Evidence |
|---|---|---|
| CPU | Intel Core i7-9850H @ 2.60 GHz, 6 physical cores / 12 logical threads, L2 1.5 MB, L3 12 MB | MEASURED |
| CPU max turbo (spec) | 4.6 GHz | ESTIMATED (Intel ARK) |
| RAM | 31.83 GiB (34,166,116,352 bytes), HP ZBook 15 G6, Micron modules | MEASURED |
| RAM max supported (spec) | 64 GB | ESTIMATED (HP spec sheet) |
| GPU 1 | NVIDIA Quadro T1000, 4.0 GiB VRAM, driver 30.0.14.7284 | MEASURED |
| GPU 2 | Intel UHD Graphics 630 (integrated), ~1 GB shared | MEASURED |
| `nvidia-smi` availability | Not found on PATH (Bash or PowerShell) | MEASURED absence |
| CUDA toolkit installed | Unknown | UNVERIFIED |
| OS | Windows 11 Pro, build 22631.6199, 64-bit | MEASURED |
| Hyper-V | Active (hypervisor detected by `systeminfo`) | MEASURED |
| Disk 0 | Seagate ST500LM021 500 GB **SATA HDD** (5400 RPM class) → drive **E:** | MEASURED |
| Disk 1 | WDC PC SN720 256 GB **NVMe SSD** → drive **C:** | MEASURED |
| Disk 2 | "Msft Virtual Disk" 50.03 GiB, file-backed virtual | MEASURED (exact volume mapping UNVERIFIED) |
| Network | Wi-Fi only (Intel AX200), linked at 81 Mbps; no Ethernet up | MEASURED |

**Important caveat on virtualization:** WMI's `Win32_Processor.VirtualizationFirmwareEnabled` reported `False`. This looks alarming but is a known WMI artifact — once Hyper-V has already claimed VT-x, WMI can report the firmware flag as unavailable even though virtualization is demonstrably working (WSL2 and Docker Desktop both require it and are installed). Not treated as a real blocker.

**Important caveat on the repo's home drive:** the AURA AI repository, and this machine's 19 GB pagefile, live on **E:**, which is the **SATA HDD**, not the NVMe SSD. The NVMe (C:) is nearly full (see §5). This inverts the usual assumption that a laptop's primary work volume is its fastest one.

---

## 3. OS / Virtualization Inventory

- Windows 11 Pro 22631.6199, last boot 2026-09-14 05:39 (≈1.75 days uptime at audit time). **MEASURED**
- WSL 2.7.14.0, kernel 6.1.18.33.2-2 (Microsoft-maintained), WSLg 1.0.73.2. **MEASURED**
- Only registered WSL distribution: `docker-desktop` (WSL2, currently **Stopped**). No general-purpose Ubuntu/Debian distro is installed. **MEASURED**
- Docker Engine 29.6.2 / Compose v5.3.1 installed; **the daemon was not running** at audit time (`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`). **MEASURED**
- Docker Desktop was deliberately **not started** for this audit — see §26 (Safety) and §12/§13 for why.

---

## 4. Docker / WSL Capacity

Because the daemon was not started, live container/image/network inventory is **UNVERIFIED** this session. What is known:

- Docker's WSL virtual disk already exists and is **not small**: `docker_data.vhdx` = **26.6 GiB**, `main\ext4.vhdx` = 104 MiB, both under `C:\Users\HP\AppData\Local\Docker\wsl\`. **MEASURED**
- This alone accounts for roughly 11% of the entire 237.6 GiB C: volume, on a drive that currently has essentially no free space left (§5).
- Docker's disk-image location defaults to C:. Relocating it to E: (79% free) via *Docker Desktop → Settings → Resources → Advanced* is a one-time, low-risk configuration change and the single highest-leverage fix available before any further Docker-based work on this machine (see §18, §32).

---

## 5. Storage Analysis

| Volume | Filesystem | Total | Free | Free % | Status |
|---|---|---:|---:|---:|---|
| **C:** (NVMe SSD) | NTFS | 237.62 GiB | **3.73 GiB** | 1.6% | CRITICAL |
| **D:** | NTFS | 19.72 GiB | 5.03 GiB | 25.5% | LOW |
| **E:** (SATA HDD) | NTFS | 465.75 GiB | 369.61 GiB | 79.4% | OK |

All figures **MEASURED**.

**What's on C:** (read-only recursive scan, MEASURED): `AppData\Local` 53.9 GB (includes the 26.6 GB Docker disk), `Windows` 36.6 GB, `Program Files` 36.9 GB, `Program Files (x86)` 15.2 GB — 142.6 GB itemized against ~233.9 GB actually used. The remainder (~91 GB) is UNVERIFIED this session (likely user-profile data outside `AppData\Local`, hibernation file, System Volume Information).

**AURA repository footprint** (E:, MEASURED, `du -h -d 1`): **16 GB total**.

| Directory | Size |
|---|---:|
| `aura_client` | 11 GB |
| `aura_backend` | 1.4 GB |
| `.git` | 1.2 GB |
| `.claude` | 1.4 GB |
| `docs` | 427 MB |
| `aura_admin` | 526 MB |
| `aura_hostd` | 60 MB |
| `.opencode` | 60 MB |
| `deploy` | 403 KB |

**Sequential I/O benchmark (E:, MEASURED):** a 256 MiB file, written then read back immediately, gave **50.1 MB/s write**. The reported "1333 MB/s read" is an OS page-cache artifact (the file was still cached from the write) and is **not** a valid cold-read figure — true cold sequential read on this HDD class is **ESTIMATED at 80–150 MB/s**, not measured directly. 50 MB/s write is consistent with a 5400 RPM 2.5" SATA HDD, not an SSD.

**Storage required by AURA (CONFIGURED + CALCULATED, since the stack has never been pulled here):**

| Item | Estimate | Evidence |
|---|---|---|
| Docker images (nginx, postgres, redis, qdrant, ollama, prometheus, alertmanager, grafana, loki, promtail, node-exporter, backend/admin builds) | 8–14 GB uncompressed, typical for this image set | ESTIMATED |
| Ollama model weights (`qwen2.5:3b-instruct-q4_K_M`) | 2.0–2.3 GB | ESTIMATED (public Ollama library figure) |
| Embedding model (`BAAI/bge-small-en-v1.5`) | ~130 MB | ESTIMATED |
| Postgres + Qdrant + Redis data volumes | Grows with usage; negligible at first boot (<500 MB) | ESTIMATED |
| Prometheus/Loki/Grafana retention (30-day default) | 1–5 GB depending on load | ESTIMATED |
| **Current AURA footprint (repo only, no Docker data yet)** | **16 GB** | MEASURED |
| **6-month growth** (repo + Docker images/volumes + model cache + logs, moderate use) | +15–25 GB | ESTIMATED |
| **12-month growth** | +30–50 GB | ESTIMATED |
| **Recommended free-space safety margin** | ≥50 GB free on whichever volume hosts Docker, at all times | ESTIMATED (standard practice, not AURA-specific) |

**Conclusion:** E: has ample room (369.6 GiB free) for all of the above, many times over, for years. **C: does not** — it cannot absorb even the smallest image pull today. This is not a capacity problem; it's a placement problem, fully solved by relocating Docker's disk image to E:.

---

## 6. Network Analysis

| Item | Value | Evidence |
|---|---|---|
| Active interface | Wi-Fi, Intel Wi-Fi 6 AX200 160MHz | MEASURED |
| Link speed | 81 Mbps | MEASURED |
| Ethernet | Not connected/up | MEASURED |
| Throughput/latency/packet-loss benchmarks | Not run | UNVERIFIED |

81 Mbps is well below the AX200 adapter's Wi-Fi 6 potential (600+ Mbps), pointing to a constrained access-point link, not an adapter limitation. This is **not a bottleneck for local AURA capacity** as scoped by this audit: Docker container-to-container and host-to-container traffic is virtual/loopback and never touches the Wi-Fi radio. It would matter only if another device (the Flutter client on a phone, say) needed to reach this machine over the network — out of scope for "can this computer run AURA locally."

---

## 7. GPU Analysis

| Item | Value | Evidence |
|---|---|---|
| GPU | NVIDIA Quadro T1000, 4 GiB VRAM, Turing (TU117), compute capability 7.5 (public spec) | MEASURED (model/VRAM) / ESTIMATED (architecture detail) |
| `nvidia-smi` | Not found on PATH | MEASURED absence |
| CUDA toolkit | UNVERIFIED |
| Currently used by AURA | **No** | CONFIGURED |

The backend's `requirements.lock` pins `torch==2.13.0+cpu` — a CPU-only wheel, chosen deliberately per the Dockerfile's own comment to avoid ~6.5 GB of CUDA/nvidia/triton packages "a CPU-only host can never execute." `docker-compose.prod.yml`'s `ollama` service has its GPU device-reservation block commented out. **As shipped, this GPU is architecturally idle for AURA** — not because it's too weak, but because the stack doesn't attempt to use it.

Could it, in principle? The compose file's optional `vllm` GPU profile defaults to `Qwen/Qwen2.5-32B-Instruct-AWQ` — a 32B model that, even 4-bit quantized, needs on the order of 18–20+ GB VRAM for weights alone (ESTIMATED, public knowledge). A 4 GB Quadro T1000 **cannot run that model under any configuration**. A much smaller GGUF/AWQ model (1–3B class) could plausibly fit in 4 GB, but that would require changing the shipped architecture (swapping the CPU torch wheel, adding a GPU-enabled inference path) — explicitly out of this audit's scope, which is to assess capacity for the *current* architecture, not redesign it.

**Conclusion: the GPU does not accelerate AURA today, and cannot usefully run the stack's own optional GPU profile on this hardware even if enabled.**

---

## 8. AURA Service Inventory

Read directly from `deploy/docker-compose.prod.yml` and `deploy/docker-compose.dev.yml` (**CONFIGURED**), not assumed from documentation.

**Production core services** (always start): `nginx`, `console` (admin dashboard), `gateway` (FastAPI/uvicorn, 4 workers), `worker-realtime` (Celery), `worker-ingestion` (Celery), `beat` (Celery scheduler), `ollama`, `postgres`, `redis`, `qdrant`, `prometheus`, `alertmanager`, `grafana`, `loki`, `promtail`, `node-exporter`, `backup`.

**Optional profiles** (off unless explicitly enabled): `sandbox` profile → `docker-socket-proxy`, `sandbox-image`, `sandbox-proxy` (Engineer agent workspaces); `training` profile → `worker-training`; `gpu` profile → `vllm`.

**Development stack** (`docker-compose.dev.yml`, project name `aura-dev`): `console`, `dev-gateway`, `worker-realtime`, `worker-ingestion`, `beat`, `postgres`, `redis`, `qdrant` — shares the *same* `ollama` instance as prod over the `aura_edge` network rather than running a second one, "to save RAM" (the compose file's own comment).

No voice/OCR/fine-tuning services run as separate containers — voice (STT/TTS) and OCR are in-process capabilities of the `gateway`/worker images (per `requirements.lock`: `faster-whisper`, `onnxruntime`); fine-tuning is the `worker-training` profile.

---

## 9 & 15. AURA Resource Requirements / Requirement Matrix

Pulled directly from each service's `deploy.resources.limits` in `docker-compose.prod.yml` (**CONFIGURED** — these are Docker's soft CPU/RAM caps, not live measurements, since the stack wasn't started).

| Service | CPU limit | RAM limit | GPU | Always running? |
|---|---:|---:|---|---|
| nginx | 0.5 | 256 MB | – | Yes |
| console (admin) | 0.5 | 512 MB | – | Yes |
| gateway (FastAPI) | 2.0 | 3 GB (1 GB reserved) | – | Yes |
| worker-realtime | 1.0 | 1 GB | – | Yes |
| worker-ingestion | 1.5 | 2 GB | – | Yes |
| beat | 0.25 | 256 MB | – | Yes |
| **ollama** | **6.0** | **10 GB** | none configured (CPU path) | Yes |
| postgres | 1.0 | 2 GB | – | Yes |
| redis | 0.5 | 768 MB | – | Yes |
| qdrant | 1.0 | 2 GB | – | Yes |
| prometheus | 0.5 | 1 GB | – | Yes |
| alertmanager | 0.25 | 256 MB | – | Yes |
| grafana | 0.5 | 512 MB | – | Yes |
| loki | 0.5 | 768 MB | – | Yes |
| promtail | 0.25 | 256 MB | – | Yes |
| node-exporter | 0.2 | 128 MB | – | Yes |
| backup | 0.5 | 512 MB | – | Yes |
| docker-socket-proxy (sandbox) | 0.25 | 128 MB | – | Off by default |
| sandbox-image (sandbox, build-only) | 0.5 | 256 MB | – | Off by default |
| sandbox-proxy (sandbox) | 0.25 | 64 MB | – | Off by default |
| worker-training (training) | 2.0 | 4 GB | – | Off by default |
| vllm (gpu) | 4.0 | 16 GB | NVIDIA, 20 GB+ VRAM class | Off by default; **not feasible on this GPU** |

**Core-service totals (always-on set): CPU limit sum ≈ 16.95 vCPU, RAM limit sum ≈ 25.13 GB.** (**CALCULATED**)

This machine has **12 logical threads**, so the CPU limit sum alone is **+41% oversubscribed** relative to available threads. Docker limits are soft caps, not reservations, so this manifests as contention under simultaneous full load rather than an outright failure — but it means the compose file was sized for an 8-vCPU host (confirmed by `ollama`'s own comment: *"6 of 8 cores for inference; leave 2 for postgres/redis/gateway/nginx"*), and this laptop has fewer physical cores than that.

---

## 10. AI Model Analysis

| Model | Role | Params | Quantization | Est. size | Evidence |
|---|---|---:|---|---:|---|
| `qwen2.5:3b-instruct-q4_K_M` | Local LLM (Ollama) | 3B | Q4_K_M GGUF | 2.0–2.3 GB | CONFIGURED (name) / ESTIMATED (size) |
| `BAAI/bge-small-en-v1.5` | Embeddings | — | fp32/fp16 (sentence-transformers) | ~130 MB | CONFIGURED (name) / ESTIMATED (size) |
| faster-whisper | STT | unspecified in reviewed config | — | UNVERIFIED | CONFIGURED (library only) |

Context window for the local model is **not explicitly pinned** in the compose environment reviewed — UNVERIFIED here; Qwen2.5-3B-Instruct's public default context is 32K tokens, but AURA may cap it lower at the application layer (not confirmed this session).

**RAM/VRAM requirement:** `ollama`'s container limit (10 GB) is generously sized for a 16 GB VPS reference point, not a measurement of the model's actual footprint. A 3B Q4_K_M GGUF model's resident memory when loaded is typically 3–5 GB (**ESTIMATED**, not measured — Ollama isn't installed on this machine). **CPU feasibility:** yes, this is the model's designed use case. **GPU feasibility:** the model could run on the Quadro T1000 (well within 4 GB), but the shipped stack doesn't attempt GPU offload (see §7).

---

## 11. Ollama / vLLM Analysis

**Ollama** (CONFIGURED, from `docker-compose.prod.yml`):
- `OLLAMA_MAX_LOADED_MODELS=1` — only one model resident at a time.
- `OLLAMA_KEEP_ALIVE=30m`
- `OLLAMA_NUM_PARALLEL=1` — **this is the concurrency ceiling.** One in-flight generation, full stop, independent of how many HTTP requests or `AURA_WORKERS` uvicorn processes are running.
- Container limits: 6.0 vCPU / 10 GB RAM.
- No GPU device reservation (commented out in the compose file).

**vLLM** (CONFIGURED, `gpu` profile, off by default): targets `Qwen/Qwen2.5-32B-Instruct-AWQ`, needs an NVIDIA GPU + container toolkit + ~20 GB+ VRAM. **Not feasible on this machine's 4 GB GPU under any configuration.**

**Concurrency reality (REFERENCE, not measured on this machine — see §12):** a real load test on comparable CPU-only hardware, using this exact model and these exact `OLLAMA_NUM_PARALLEL`/`AURA_PROVIDER_LOCAL_MAX_CONCURRENCY` settings, found **flat throughput from 1 to 8 concurrent requests** — the semaphore, not CPU headroom, is the limiter. That result would reproduce on this laptop's hardware as-is: **1 concurrent AI request, full stop**, unless `OLLAMA_NUM_PARALLEL` is deliberately raised — and raising it would likely just make every concurrent generation slower (they'd time-share the same 6–12 threads), not add real throughput, per the same report's own finding.

No concurrency level from 1 through 16 was tested *on this machine*, for the reasons in §26. 1 is the only level this configuration supports in practice regardless of hardware, per the reference evidence above.

---

## 12. Real AI Inference Benchmark — NOT RUN ON THIS MACHINE

**This section was not executed as a live benchmark on this laptop.** Reason (see §26 for full reasoning): C: had 3.73 GiB free at audit time, Ollama is not installed natively, and the Docker stack was not started to avoid risking that free space. Running Tests A–E (1/2/4/8/16 concurrent requests against a live model) would have required pulling several GB of container images and a ~2 GB model onto a drive that cannot currently absorb them.

**In its place**, the following REFERENCE data — a real measurement, on different but comparable CPU-only hardware, using the identical model and identical `OLLAMA_NUM_PARALLEL=1` configuration — is used, from `docs/reports/phase-a4-load-test.md` (run 2026-08-23, 4-core/15 GB Linux sandbox, real Postgres/Qdrant/Redis/Ollama, not in-memory adapters):

| Metric | Value |
|---|---|
| Single-turn latency, p50 | 56.3 s |
| Single-turn latency, p95 | 70.5 s |
| Single-turn latency, max | 70.5 s |
| Pipeline overhead only, p50 | 244 ms |
| Pipeline overhead only, p95 | 19.0 s (CPU contention artifact on that 4-core sandbox) |

| Concurrency | Wall time | Throughput | Per-turn p50 | Per-turn max |
|---:|---:|---:|---:|---:|
| 1 | 28.1 s | 0.04 turns/s | 28.1 s | 28.1 s |
| 2 | 36.0 s | 0.06 turns/s | 27.3 s | 36.0 s |
| 4 | 81.5 s | 0.05 turns/s | 48.1 s | 81.5 s |
| 8 | 143.9 s | 0.06 turns/s | 85.2 s | 143.9 s |

**CALCULATED extrapolation to this machine:** this laptop has more physical cores (6c/12t vs. 4c) and over 2× the RAM (31.8 GB vs. 15 GB) of the A4 sandbox, so per-turn latency would plausibly be *similar or modestly better* — CPU-bound GGUF token generation for a 3B model scales sublinearly with core count past ~4–6 cores due to memory-bandwidth limits. The concurrency ceiling (effectively 1 request at a time) would reproduce **identically**, because it's config, not hardware.

**Not tested at all, on any hardware, per that report's own "still open" list:** a run with `OLLAMA_NUM_PARALLEL` raised above 1, to find where CPU contention (rather than the semaphore) becomes the real limiter. That would be a legitimate next step *after* the storage blocker is cleared — see §29's reproduction commands.

---

## 13. Full AURA Integration Load Test — NOT RUN ON THIS MACHINE

Same reasoning as §12: the full stack was never started this session. No startup time, total resource usage under load, or per-workload latency (chat / RAG / memory / tool call / planning / Engineer task / concurrent users) was measured here. All of it is **UNVERIFIED** for this machine specifically. §25's reproduction commands describe exactly how to produce this data once §26's blocker is cleared.

---

## 14–17. Resource Budgets

### CPU budget (CALCULATED)

```
12 logical threads (6 physical cores, SMT)
- OS + desktop-tool baseline, AS OBSERVED LIVE: ~76.8% of 12 threads already in use
  (Cursor, Chrome x3, Claude Code, ChatGPT desktop, Flutter/Dart dev processes - not AURA)
- Core AURA compose CPU limits (soft caps): 16.95 vCPU requested
= Requested (16.95) alone exceeds total threads (12) by +41%, BEFORE counting the
  desktop tools that were already consuming ~9 of those 12 threads at audit time.
```
CPU is a **binding constraint under simultaneous full load**, though soft limits mean it degrades (contention, slower responses) rather than hard-failing.

### RAM budget (CALCULATED)

```
31.83 GB physical
- Observed live usage with ZERO AURA services running: ~27.14 GB (only 4.69 GB available)
- Core AURA compose RAM limits (soft caps): 25.13 GB
= Even under a best-case "close everything else first" assumption, 31.83 GB total
  minus a conservative ~2.5 GB idle-Windows baseline minus ~2-4 GB Docker
  Desktop/WSL2 VM overhead (ESTIMATED, daemon wasn't run to measure it live) leaves
  roughly 25-27 GB for AURA - almost exactly consumed by the 25.13 GB core-service
  limit sum, i.e. near-zero headroom even in the best case.
= Under the ACTUAL observed condition (desktop tools left open, 27 GB already used),
  there is a RAM deficit on the order of 20+ GB if the full core stack were added
  on top without closing anything.
```
**RAM is the tightest resource on this machine for running the full core stack, more binding than CPU.** A trimmed subset (see §16 Mode B-lite) is far more realistic:
```
gateway 3G + postgres 2G + redis 0.75G + qdrant 2G + ollama 10G = 17.77 GB
```
— still large chiefly because `ollama`'s 10 GB limit carries generous VPS-sized headroom (actual resident use for a loaded 3B model is plausibly 3–5 GB, ESTIMATED, not measured).

### GPU/VRAM budget (CALCULATED)

```
4.0 GiB total VRAM (Quadro T1000)
- Not used by the current architecture (CPU-only torch, no Ollama GPU offload configured)
= Effectively 4.0 GiB fully idle/available, but moot: the stack doesn't call on it,
  and the one optional path that would (vllm profile, 32B AWQ model) needs ~5x more
  VRAM than exists on this GPU.
```

### Storage budget (CALCULATED)

```
C: 237.62 GiB total - 3.73 GiB free  -> 0 GiB safely usable for new Docker data today
E: 465.75 GiB total - 369.61 GiB free -> ~369 GiB usable, comfortably covers years of growth
- Recommended margin: keep >=50 GB free at all times on whichever drive hosts Docker
= Safe remaining storage: effectively zero on C: until Docker's disk image is relocated;
  effectively "not a constraint" on E: once it is.
```

---

## 18. Thermal & Sustained Performance

> Thermal validation could not be verified on this platform. No CPU/GPU temperature sensors, throttling counters, or sustained-clock telemetry were read this session — the tools available in this environment do not expose HP ZBook thermal sensors, and no benchmark was run long enough to be thermally meaningful (see §26 for why sustained load wasn't attempted). **UNVERIFIED.**

---

## 19. Local Deployment Modes

| Mode | Description | Feasibility | Evidence |
|---|---|---|---|
| **A — Development** | `pytest` against in-memory adapters, no Docker, no model, no network (per the project's own `CLAUDE.md`) | **FULLY FEASIBLE** | CONFIGURED (documented default); not freshly re-run this session — UNVERIFIED as executed here, but requires negligible resources by design |
| **B — Full Local AURA** | All core compose services simultaneously | **BLOCKED today** by C: free space; **PARTIALLY FEASIBLE** once relocated, and only realistic with desktop tools closed or a trimmed service set (drop monitoring stack + nginx + console + backup) | CALCULATED (§14–17) |
| **C — Full Local + AI Workload** | Mode B + concurrent AI requests | Capped at **1 effective concurrent AI request** (§11), ~28–70 s/turn | REFERENCE + CALCULATED |
| **D — Production-like (prod + dev stacks side by side, mirroring the VPS)** | Both compose projects running simultaneously, as the VPS does | **NOT FEASIBLE** — this machine can't comfortably fit *one* full core stack; doubling it is well beyond capacity | CALCULATED |

---

## 20. Capacity Tiers

| Tier | Definition | Supported here? |
|---|---|---|
| Tier 0 — Development | 1 developer, `pytest` only | **Yes**, today |
| Tier 1 — Full Local | Complete system, low concurrency | **Feasible with limitations** — only after the storage fix in §32, and only with a trimmed service set or desktop tools closed; not verified live this session |
| Tier 2 — Small Deployment (1–5 concurrent users) | — | **Not supported as configured** — `OLLAMA_NUM_PARALLEL=1` serializes all AI requests regardless of user count; users beyond the first simply queue |
| Tier 3 / Tier 4 | 5–20 / 20–50+ concurrent users | **Not supported** — same reason, more so |

No tier above 1 was tested; none is claimed beyond what §11's reference data and the compose configuration itself support.

---

## 21. Bottleneck Analysis

| # | Bottleneck | Class | Evidence | Impact | Severity | Recommended solution |
|---|---|---|---|---|---|---|
| 1 | **C: free space (3.73 GiB)** | Storage | MEASURED | Blocks any Docker image pull/build; blocked this entire audit's live-benchmark sections | **CRITICAL** | Relocate Docker Desktop's disk-image location to E: (Settings → Resources → Advanced), or free ≥30 GB on C: |
| 2 | **RAM headroom** | RAM | MEASURED (4.7 GB available with 0 AURA services running) + CALCULATED (25.1 GB core-service limit sum) | Full core stack does not fit alongside normal desktop use | **CRITICAL/HIGH** | Close desktop apps before testing; run a trimmed service subset; consider a RAM upgrade (this chassis supports up to 64 GB per spec, ESTIMATED) |
| 3 | **Repo/Docker host volume is HDD, not SSD** | Storage I/O | MEASURED (50.1 MB/s write on E:) | Postgres/Qdrant/Docker-layer I/O will be materially slower than on the NVMe | **HIGH** | Free space on the NVMe (C:) and relocate the working set there, or accept HDD-class I/O |
| 4 | **`OLLAMA_NUM_PARALLEL=1` concurrency ceiling** | Model serving / architecture | REFERENCE (Phase A4, flat throughput 1→8 concurrent) | Single-flight AI generation regardless of core count | **HIGH** | Accept single-flight local inference; concurrency would require a GPU-equipped host and a config change, out of this audit's scope |
| 5 | **GPU unusable by the shipped stack** | Architecture | CONFIGURED (CPU-only torch, no GPU offload wired) | Quadro T1000 sits idle | **MEDIUM** | None within current architecture; would require a deliberate config change to exercise it |
| 6 | **CPU oversubscription (16.95 vCPU limits vs 12 threads)** | CPU | CALCULATED | Contention/slowdown under simultaneous full load, not a hard failure (soft limits) | **MEDIUM** | Trim always-on services; accept degraded throughput under full load |
| 7 | **Wi-Fi link speed (81 Mbps)** | Network | MEASURED | Irrelevant to local container traffic; matters only for remote client access | **LOW / informational** | N/A for local capacity |

---

## 22. Hardware Upgrade Simulation

| Scenario | Effect (CALCULATED/ESTIMATED from the numbers above) |
|---|---|
| **Current computer** | i7-9850H (6c/12t), 31.8 GB RAM, Quadro T1000 4GB, C: 3.7GB free / E: 369.6GB HDD free |
| **Free C: / relocate Docker disk to E:** | Removes bottleneck #1 entirely at zero hardware cost — the single highest-leverage change available. Does not change RAM or CPU headroom. |
| **RAM upgrade (e.g., to 64 GB, the chassis's public max)** | Directly resolves bottleneck #2 — the 25.1 GB core-service limit sum would then coexist comfortably with a full desktop session, and Tier 1 (§20) would become verifiable without closing other apps. Highest-value single upgrade for *this* stack, since it's the tightest observed resource. |
| **GPU upgrade** | Would not help *this* architecture as shipped (CPU-only torch, no GPU offload wired — §7). Only relevant if AURA's own config were changed to add a GPU inference path, which is outside this audit's scope to recommend. |
| **CPU upgrade** | This is a fixed laptop CPU (BGA, not socketed) — not practically upgradable. Even if it were, §21's #4 (concurrency ceiling) is a config limit, not a CPU limit, so a faster CPU would mainly shave per-turn latency, not raise concurrency. |
| **Maximum practical upgrade on this platform** | RAM to 64 GB + Docker disk relocated to E: (or C: freed/expanded). This combination removes both CRITICAL bottlenecks without touching the CPU or GPU, which are the two components this chassis doesn't allow upgrading anyway. |

---

## 23. Local vs VPS Comparison

The production VPS's own configuration (`deploy/.env.example`, `docker-compose.prod.yml` comments) documents its target as an **8-vCPU, CPU-only** host with **a documented 24 GB RAM reference point**, and Phase A4's real measurement (§12) was captured on a 4-core/15 GB sandbox running the same model.

| | This computer | Minimum VPS (AURA's own documented reference) | Recommended VPS (per roadmap/A4 evidence) |
|---|---|---|---|
| vCPU | 6 physical / 12 threads | 4 (A4-tested floor) | 8 (compose's own sizing comments) |
| RAM | 31.8 GB | 15 GB (A4-tested) | 16–24 GB (compose/`.env.example` reference) |
| GPU | Quadro T1000, 4GB, unused by config | None (CPU-only by design) | None (CPU-only by design); optional GPU node only for the separate `vllm` hybrid path |
| Storage | 369.6 GB free (E:, HDD) once relocated; 3.7 GB free (C:, NVMe) today | Sized per §5's growth table | Same, plus headroom for Prometheus/Loki retention |
| Expected concurrency | 1 (config ceiling) | 1 (config ceiling, A4-confirmed) | 1 unless `OLLAMA_NUM_PARALLEL` is deliberately raised and re-tested |
| Main limitation | Storage placement (fixable at no cost) + RAM headroom vs. desktop use | Same `OLLAMA_NUM_PARALLEL=1` ceiling A4 found | Same ceiling; a VPS doesn't remove it either — it's config, not hardware, on either side |

**This machine already meets or exceeds AURA's own documented minimum VPS reference on every axis except vCPU count** (6 physical cores here vs. 8 vCPU documented) — and even that gap is partly offset by 12 logical threads and over 2× the RAM of the actual A4 test environment. The thing standing between this laptop and a working local stack is not undersized hardware; it's 3.7 GB of free space on the wrong drive.

---

## 24. Pass/Fail Matrix

| Requirement | Available | Required (compose-configured) | Margin | Status |
|---|---|---|---|---|
| CPU (threads vs. core-service vCPU limits) | 12 threads | 16.95 vCPU | −4.95 | **FAIL** (soft; degrades rather than hard-fails) |
| RAM (physical vs. core-service limits) | 31.83 GB | 25.13 GB | +6.7 GB nominal / **−20+ GB as actually observed** with desktop tools open | **MARGINAL / FAIL under real conditions** |
| GPU | Quadro T1000, 4 GB | Not required by current config | N/A | **N/A — not used** |
| VRAM (for optional `vllm` profile only) | 4 GB | ~20 GB+ for default 32B AWQ model | −16 GB | **FAIL** (profile not usable, but also not required for the core stack) |
| Storage — C: (Docker's default location) | 3.73 GiB free | Several GB for images + model | −several GB | **FAIL** |
| Storage — E: (repo location, relocation target) | 369.6 GiB free | ~16 GB current + growth | +350 GiB | **PASS** |
| Network | 81 Mbps Wi-Fi | Not a factor for local traffic | N/A | **PASS (not applicable)** |
| Docker | Installed, daemon not running | Required | — | **UNVERIFIED (not started)** |
| WSL | 2.7.14.0, only docker-desktop distro | Required for Docker Desktop | — | **PASS** |
| Model inference (Ollama) | Not installed/tested locally | CPU inference of a 3B Q4 model | — | **UNVERIFIED locally; REFERENCE evidence supports feasibility** |
| PostgreSQL / Qdrant / Redis | Not tested locally | Per compose limits | — | **UNVERIFIED locally; CONFIGURED limits fit within budget once RAM/storage are resolved** |
| Monitoring stack (Prometheus/Grafana/Loki) | Not tested locally | ~3.3 GB RAM combined limit | — | **UNVERIFIED locally; likely fine in isolation, contributes to overall RAM tightness** |
| Engineer / Sandbox | Off by default | Optional | — | **N/A unless enabled** |
| Full stack simultaneously | Not tested locally | ~25.1 GB RAM / 16.95 vCPU | See RAM/CPU rows | **FAIL under current C: state; MARGINAL once storage is fixed** |

---

## 25. Final Local Feasibility Decision

# FEASIBLE WITH LIMITATIONS

This is not a "no" dressed up politely, and it is not an unqualified "yes" either. Specifically:

- **The hardware class is right.** This machine meets or exceeds AURA's own documented minimum reference deployment (§23) on RAM and roughly matches it on CPU threads. Nothing here requires new hardware to work *at all*.
- **One precondition blocks everything right now**: C: has 3.73 GiB free and cannot absorb the images/model AURA needs. This is a **zero-cost fix** (relocate Docker's disk image to E:, or free space on C:) — not a hardware limitation.
- **Once that's fixed, RAM is the next real constraint**, not a hard failure but a tight fit: the core compose stack's own limits (25.1 GB) leave little headroom against this machine's actual, observed, non-idle RAM usage (27 GB already in use with zero AURA services running). Running the full stack requires either closing other applications or trimming which services run at once.
- **AI throughput is single-flight by design**, confirmed on comparable hardware (Phase A4), not discovered as a surprise limitation here — this is documented, intentional behavior (`OLLAMA_NUM_PARALLEL=1`) appropriate for a CPU-only single-user local deployment, not a defect.
- **The GPU is a non-factor** — present, but unused by the current architecture, and too small for the one optional path (`vllm`) that would use a GPU.

Nothing here is hidden behind a general "yes." The concrete blockers, and their remedies, are itemized in §21 and §32.

---

## 26. Safety Notes — What Was Deliberately Not Done, and Why

Per this audit's own rules ("do not consume all available disk," "stop benchmarks before the system becomes unsafe," "do not modify the system merely to make the benchmark pass"), the following were **not** performed:

1. **Docker Desktop was not started with the intent to pull/build images**, and no `docker compose up` was run. C: had 3.73 GiB free at the start of this audit; Docker's own WSL disk already occupies 26.6 GiB there. Pulling the stack's images (nginx, postgres, redis, qdrant, ollama, prometheus, alertmanager, grafana, loki, promtail, node-exporter, plus building the backend/admin images) would add multiple more GB to an already-critical drive.
2. **No Ollama model was downloaded** (Ollama isn't even installed natively; the containerized path was avoided for the same disk-space reason).
3. **`deploy/secrets/` does not exist locally** — only `deploy/.env*` files. `bootstrap-secrets.sh` has never been run here, so the production compose file cannot start as-is; generating secrets is a setup action beyond a read-only audit's scope to perform unilaterally.
4. **`deploy/.env` (uncommitted, present locally) is pre-populated with the production VPS's own values** — `AURA_DOMAIN=169.58.188.43.nip.io` and `AURA_SANDBOX_ENABLED=true`. It reads as a working copy of the production environment file, not a local-only configuration. It was read (to check provider/model settings) but never modified or used to start anything.
5. **No sustained/thermal stress test was run** — no benchmark long enough to be thermally meaningful was attempted, consistent with not running live inference at all this session.

None of this was destructive, and nothing on the system was changed by this audit beyond two temporary benchmark files (a 256 MiB disk-speed test file on E:, deleted immediately by the same command that created it).

---

## 27. Recommended Configuration

**For development (Tier 0, works today):** `pytest` in `aura_backend/` against the in-memory adapters — no Docker, no model, minimal resources. No changes needed.

**For full local integration (Tier 1, after the storage fix):**
1. Relocate Docker Desktop's disk-image location to E: (Settings → Resources → Advanced).
2. Run `bootstrap-secrets.sh` to generate local secrets (do not reuse the production `.env` as-is).
3. Start a **trimmed** core set first rather than the full compose file: `postgres`, `redis`, `qdrant`, `ollama`, `gateway` — skip `nginx`, `console`, the monitoring stack (`prometheus`/`grafana`/`loki`/`alertmanager`/`promtail`/`node-exporter`), and `backup` for a first local smoke test. This drops the RAM limit sum from ~25.1 GB to ~17.8 GB, a far more comfortable fit alongside normal desktop use.
4. Close unrelated desktop applications (Cursor, extra Chrome tabs, ChatGPT desktop) before testing — they were consuming ~9 of 12 logical threads and ~27 GB of RAM at audit time, entirely independent of AURA.
5. Expect **~30–70 seconds per AI turn**, **one turn at a time** — this is the documented, tested behavior of this configuration, not a fault to troubleshoot.

**For AI inference specifically:** the current `qwen2.5:3b-instruct-q4_K_M` / `OLLAMA_NUM_PARALLEL=1` configuration is appropriate for this hardware class and matches what AURA's own reference deployment uses. No change recommended without a specific reason to trade latency for concurrency (which would require a different host, not a different setting on this one).

**For stress/production-like testing:** not realistic on this machine as configured (§19 Mode D) — use a VPS or a dedicated Linux sandbox as Phase A4 did.

---

## 28. Risks

- **Storage exhaustion on C: is an active, present-tense risk**, independent of this audit — 1.6% free on a system drive can cause Windows Update failures, application crashes, or worse if it hits zero. This should be addressed regardless of AURA plans.
- **Running the full stack without first checking RAM availability** could push this machine into heavy paging/thrashing, given the tight margin found in §14.
- **The local `deploy/.env`'s production values** (VPS domain, sandbox enabled) mean a careless `docker compose up` using that file as-is could produce confusing behavior (cert requests for a domain this machine doesn't own, sandbox host-root paths that don't exist on Windows) rather than a clean local error.
- **`AURA_SANDBOX_ENABLED=true` in the local `.env`** means enabling the `sandbox` profile locally would attempt to wire up the Engineer agent's Docker-socket-proxy path — not evaluated for correctness on Windows/WSL2 in this audit.

---

## 29. Unverified Items

Explicitly listed per this audit's own evidence-classification rule — none of the following were established this session:

- Exact per-DIMM RAM capacity/speed (WMI returned blank fields for both modules).
- CUDA toolkit installation status on this machine (`nvidia-smi` absent from PATH; only the WDDM display driver was confirmed).
- Cold (non-cached) sequential/random read speed and IOPS for the E: HDD.
- Live Docker container resource usage, image sizes, network/volume configuration (daemon not started).
- Actual startup time, RAM/CPU/GPU usage, and per-workload latency for a running AURA stack on this machine (chat, RAG, memory, tool invocation, planning, Engineer task, concurrent users — §13's Workloads 1–7).
- Inference benchmark Tests A–E (1/2/4/8/16 concurrent requests) run natively on this machine.
- Thermal behavior under sustained load (no sensors read; no sustained load run).
- Exact disk-to-volume mapping for the "Msft Virtual Disk" (50.03 GiB) device.
- The ~91 GB unitemized remainder of C:'s used space beyond `AppData\Local`/`Windows`/`Program Files`(x86).
- Whether `AURA_SANDBOX_ENABLED=true` and the Engineer/Docker-socket-proxy path work correctly under WSL2 on this specific machine.

---

## 30. Reproduction Commands

**Hardware/OS discovery (all read-only):**
```powershell
Get-CimInstance Win32_Processor | Select Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed
Get-CimInstance Win32_ComputerSystem | Select TotalPhysicalMemory,Manufacturer,Model
Get-CimInstance Win32_VideoController | Select Name,AdapterRAM,DriverVersion
Get-CimInstance Win32_OperatingSystem | Select Caption,Version,BuildNumber
Get-PhysicalDisk | Select DeviceId,FriendlyName,MediaType,Size,BusType
Get-Volume | Select DriveLetter,FileSystem,Size,SizeRemaining
wsl --status; wsl -l -v
Get-NetAdapter | Where Status -eq Up | Select Name,LinkSpeed
Get-Counter '\Memory\Available MBytes'
Get-Counter '\Processor(_Total)\% Processor Time'
```

**Docker/repo discovery:**
```bash
docker --version && docker compose version && docker info
du -h -d 1 "E:/AURA AI"
```

**Disk speed (safe, self-cleaning):**
```powershell
$f="E:\bench_tmp.bin"; $d=New-Object byte[](256MB); (New-Object Random).NextBytes($d)
$sw=[Diagnostics.Stopwatch]::StartNew(); [IO.File]::WriteAllBytes($f,$d); $sw.Stop()
"{0} MB/s write" -f (256000/$sw.ElapsedMilliseconds); Remove-Item $f -Force
```

**To actually run the live benchmarks this report could not (only after freeing/relocating storage per §32):**
```bash
cd "E:/AURA AI/deploy"
# 1. Relocate Docker's disk image to E: first (Docker Desktop GUI), then:
./scripts/bootstrap-secrets.sh
docker compose -f docker-compose.prod.yml --env-file .env up -d postgres redis qdrant ollama gateway
cd ../aura_backend
pytest tests/load -m load -s          # real concurrency sweep against real services
pytest tests/benchmarks -m bench      # wall-clock inference benchmarks
```

---

## 31. Raw Benchmark Results

See [`docs/AURA_LOCAL_CAPACITY_RAW.json`](AURA_LOCAL_CAPACITY_RAW.json) for every figure in this report in structured form, each tagged with its evidence class.

---

## 32. Final Recommendation

**Current Computer:** HP ZBook 15 G6 — Intel i7-9850H (6c/12t, 2.6 GHz base), 31.83 GB RAM, NVIDIA Quadro T1000 (4 GB VRAM, unused by AURA's current config), 256 GB NVMe (C:, 3.7 GB free) + 500 GB SATA HDD (E:, 369.6 GB free, hosts the repo).

**Full AURA Local Status:** **FEASIBLE WITH LIMITATIONS**

**Current Maximum Practical Load:** Tier 0 (development/`pytest`) works today. Tier 1 (full local stack) is blocked by storage placement, not hardware, and was not live-tested this session — see §29.

**Primary Bottleneck:** C: free space (3.73 GiB), immediately followed by RAM headroom against real (non-idle) desktop usage.

**Required Upgrade:** None strictly required. A RAM upgrade toward 64 GB (chassis maximum, per spec) is the highest-value *optional* upgrade, since RAM is the tightest resource even after the storage fix.

**Recommended Local Configuration:** Relocate Docker's disk image to E:; run the trimmed core set (`postgres`, `redis`, `qdrant`, `ollama`, `gateway`) rather than the full compose file for day-to-day local development; add monitoring/nginx/console only when specifically needed.

**Recommended VPS Configuration:** Match what's already documented and measured elsewhere in this repo — an 8-vCPU, 16–24 GB RAM, CPU-only host (`docs/reports/phase-a4-load-test.md`, `deploy/.env.example`) — for anything beyond single-developer local use, since `OLLAMA_NUM_PARALLEL=1` caps useful concurrency at 1 on *any* host, local or VPS, under the current configuration.

**Upgrade Priority:**
1. Relocate Docker Desktop's disk image from C: to E: (zero cost, removes the CRITICAL blocker).
2. Free general space on C: regardless of AURA (1.6% free is a systemic Windows risk on its own).
3. RAM upgrade toward 64 GB, if sustained full-stack local development becomes a regular need.

**Production Transition Trigger** (evidence-derived from this audit's own numbers): move inference or other services to a VPS when any of the following hold on this machine —
- Available RAM consistently below ~3 GB with the core stack running (this audit measured 4.7 GB available with *zero* AURA services running, so this threshold is close by default).
- More than one concurrent AI user is needed — `OLLAMA_NUM_PARALLEL=1` makes this a hard functional wall locally, not a soft degradation, per §11/§12.
- C: (or wherever Docker's disk lives) drops below the ≥50 GB safety margin recommended in §5.
- Sustained CPU utilization above ~85% for extended periods (this audit observed 76.8% from desktop tools *alone*, with no AURA load).

---

*This report and its raw data were produced by directly inspecting this machine (WMI, WSL, Docker CLI, filesystem) and this repository's own compose/environment files during a single session on 2026-09-16. No Docker container was started, no image was pulled, and no model was downloaded, for the storage-safety reasons documented in §26. Sections describing what those live tests would show are built from the repository's own configuration plus a real reference benchmark run previously on comparable hardware, clearly labeled throughout.*
