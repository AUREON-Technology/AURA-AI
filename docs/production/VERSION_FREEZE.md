# Version Freeze

The exact versions AURA currently runs or builds with, read from the tree at `11abb50`
(2026-09-24). This is the baseline Phase 0 freezes. Any change to a row below is a deliberate,
reviewed act, recorded here in the same PR. Every unpinned item is a gap in
[gap_register.yaml](gap_register.yaml).

Legend: **digest** = immutable (`@sha256:` or a file sha256 check). **exact** = an exact version,
where the tag or index could still be re-pushed. **tag** = a mutable tag. **floating** = `latest`,
`stable` or a range.

## 1. Language runtimes and toolchains

| Item | Version | Pin level | Source | Gap |
| --- | --- | --- | --- | --- |
| Python (gateway/worker image) | `python:3.11-slim` | tag | `aura_backend/Dockerfile:4,33` | GAP-032 |
| Python (CI) | `3.11` | minor | `backend-ci.yml:43,103`, `eval-ci.yml:31` | — |
| Python (sandbox image) | `python:3.12-slim@sha256:78387bc3…184ea` | digest | `deploy/sandbox/Dockerfile:12` | — |
| Flutter (CI) | `3.44.6` | exact | `client-ci.yml:36,68,102,142` | — |
| Flutter (release builds) | `channel: stable` | **floating** | `release-apk.yml:30-32,76-80` | GAP-044 |
| Dart SDK constraint | `^3.11.4` | range, with lockfile | `aura_client/pubspec.yaml:22`, `aura_hostd/pubspec.yaml:10` (+ `pubspec.lock` in both) | — |
| Dart (hostd CI) | `3.11.4` | exact | `hostd-ci.yml:30` | — |
| Java (Android build) | `17` (temurin) | major | `client-ci.yml:73` | — |
| Node (admin CI) | `22` | major | `admin-ci.yml:34` | — |
| Node (admin image) | `node:22-alpine` | tag | `aura_admin/Dockerfile:1,6,22` | GAP-032 |
| Next.js / React / Playwright | `^15.3.0` / `^19.0.0` / `^1.52.0` in `package.json`. The resolved versions are locked in `aura_admin/package-lock.json` (`npm ci`) | lockfile | `aura_admin/package.json:13-18` | — |
| GitHub Actions | `@v4`/`@v5`/`@v2` major tags | tag | all workflows | GAP-045 |

## 2. Python dependencies (`aura_backend/requirements.lock`)

Fully locked by `uv pip compile` (the header comment has the exact command). Don't hand-edit.
Key pins:

| Package | Version | Package | Version |
| --- | --- | --- | --- |
| fastapi | 0.141.1 | starlette | 1.6.0 |
| uvicorn | 0.52.4 | pydantic | 2.13.4 |
| httpx | 0.28.1 | sqlalchemy | 2.0.52 |
| alembic | 1.19.1 | asyncpg | 0.31.0 |
| **celery** | **5.6.3** | kombu | 5.6.2 |
| redis (client) | 8.1.0 | qdrant-client | 1.19.0 |
| torch | 2.13.0+cpu (CPU index) | transformers | 5.15.1 |
| sentence-transformers | 6.0.0 | numpy | 2.4.6 |
| faster-whisper | 1.2.1 | ctranslate2 | 4.8.1 |
| onnxruntime | 1.29.0 | pypdf | 6.16.1 |
| pytesseract | 0.3.13 | prometheus-client | 0.26.0 |
| pyjwt | 2.13.0 | cryptography | 50.0.0 |
| mcp | 2.1.1 | docker | 7.2.0 |
| pyyaml | 6.0.3 | | |

Not in the lock: `peft`, `tensorflow`. The e8a960b ML service imports them lazily, and they're
absent from the image (GAP-017). Dev and test tooling (`requirements-dev.txt`) uses ranges:
`pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=5.0`, `testcontainers[postgres]>=4.0.0`,
`pip-tools>=7.4` (GAP-033).

## 3. Container images (`deploy/docker-compose.prod.yml` unless noted)

| Service | Image | Pin level | Line | Gap |
| --- | --- | --- | --- | --- |
| nginx | `nginx:1.27-alpine` | tag | 142 | GAP-032 |
| docker-socket-proxy (sandbox profile) | `tecnativa/docker-socket-proxy:0.3.0` | tag | 444 (dev 239) | GAP-032 |
| sandbox-image | built from `deploy/sandbox/Dockerfile` (digest-pinned base) | digest (base) | 482-487 | — |
| sandbox-proxy | `vimagick/tinyproxy:latest` | **floating** | 506 (dev 285) | GAP-032 |
| ollama | `ollama/ollama:latest@sha256:9d30908e…c330ad` | digest | 667 | — |
| vllm (gpu profile) | `vllm/vllm-openai:latest` | **floating** | 709 | GAP-032 |
| postgres | `postgres:16-alpine` | tag | 737 (dev 406) | GAP-032 |
| redis | `redis:7-alpine` | tag | 762 (dev 430) | GAP-032 |
| qdrant | `qdrant/qdrant:latest@sha256:057ee3a8…d1fc` | digest | 789 (dev 455) | — |
| prometheus | `prom/prometheus:latest@sha256:508729e0…9b69` | digest | 807 | — |
| alertmanager | `prom/alertmanager:latest@sha256:690c7b52…340d` | digest | 825 | — |
| grafana | `grafana/grafana:latest@sha256:ab5cb380…9b43` | digest | 851 | — |
| loki | `grafana/loki:latest@sha256:efd47c67…5952` | digest | 877 | — |
| promtail | `grafana/promtail:latest@sha256:6cfa64ec…1381` | digest | 889 | — |
| node-exporter | `prom/node-exporter:latest@sha256:1b4e4438…1be0` | digest | 903 | — |
| backup | built from `deploy/backup/Dockerfile` (`FROM postgres:16-alpine`, plus apk rclone/wget/redis unpinned) | tag | 920-923 | GAP-032 |
| gateway / workers / beat | built from `aura_backend/Dockerfile` | base tag | 222+ | GAP-032 |
| console | built from `aura_admin/Dockerfile` | base tag | 181+ | GAP-032 |
| HF Space images (not prod) | `vllm/vllm-openai:latest`, `ghcr.io/ggml-org/llama.cpp:server` | **floating** | `deploy/spaces/*/Dockerfile` | GAP-032 |

The full digests are in the compose file. They're abbreviated here only for width.

## 4. Model artefacts

| Artefact | Reference | Pin level | Source | Gap |
| --- | --- | --- | --- | --- |
| Chat LLM | `qwen2.5:3b-instruct-q4_K_M` (Ollama) | **tag** | compose 256, `.env.example:77-78,187` | GAP-030 |
| Embedder | `BAAI/bge-small-en-v1.5`, dim 384 | **no revision** (runtime HF fetch) | compose 289-290, `aura_core/llm/embeddings.py:358` | GAP-031 |
| Reranker | heuristic (default). Opt-in `cross-encoder/ms-marco-MiniLM-L-6-v2` has no revision | n/a / **no revision** | `settings.py:394-395` | GAP-031 |
| STT | faster-whisper `tiny.en` | **no revision** (runtime fetch) | `settings.py:1048`, `whisper_stt.py:63` | GAP-031 |
| TTS | Piper `2023.11.14-2`, voices `en_US-lessac-medium`, `ar_JO-kareem-medium` @ `rhasspy/piper-voices` `v1.0.0` | digest (sha256 at build) | `aura_backend/Dockerfile:44-80` | — |
| OCR / fallback TTS | Debian `tesseract-ocr`, `espeak-ng` (apt) | floating | `aura_backend/Dockerfile:60-61` | GAP-032 |
| vLLM default model (gpu profile, never run) | `Qwen/Qwen2.5-32B-Instruct-AWQ` | **no revision** | compose 713 | GAP-030 |

## 5. Freeze rules (effective with Phase 0)

1. Any PR that changes a row above updates this file and [MODEL_REGISTRY.md](MODEL_REGISTRY.md)
   in the same commit.
2. New image references must be `tag@sha256:…`. New model loads must pass an immutable revision.
3. The lockfile is regenerated only with the command in its header, never hand-edited.
4. Toolchain bumps (Python, Flutter, Dart, Node) land with green CI for every affected app. As of
   2026-09-24, that requires GAP-043 (Actions not running) to be fixed first.
