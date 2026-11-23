---
title: AURA AI Backend
emoji: 🤖
colorFrom: purple
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
---

# AURA AI Backend

FastAPI server for the AURA AI platform — a self-hosted AI operating system with hybrid retrieval, durable memory, tool use, and self-hosted models behind a v3-only HTTP gateway.

## Architecture

```
HTTP ──▶ Gateway (auth, rate limit, validation) ──▶ AI Kernel
                                                  │
    ┌──────────┬───────┼────────┬──────────┬─────────┬──────────┐
    ▼          ▼       ▼        ▼          ▼         ▼          ▼
 Safety   Prompts  Memory  Knowledge   Tools   Planning   Reasoning
    │          │       │        │          │         │          │
    └──────────┴───────┴────────┴──────────┴─────────┴──────────┘
                                                  │
                                          LLM Router ──▶ Ollama · vLLM · any OpenAI-compatible endpoint you host
```

**Hexagonal layering** (enforced by AST-based fitness tests in `tests/architecture/`):

```
domain/  ports/          stdlib only — entities, value objects, Protocols
engines                  domain + ports only (prompts, context, memory, knowledge, tools, safety, planning, reasoning, execution, llm)
adapters/  api/          vendor SDKs, imported lazily inside functions, not at module scope
container.py             the only place concrete meets abstract
```

The arrow never reverses. `container.py` is the single wiring point — every adapter is constructed lazily.

## Key Components

- **`aura_service.py`** — v3-only gateway, never imports legacy `main.py`
- **`aura_core/`** — hexagonal core: domain, ports, engines, adapters
- **AI Kernel** (`aura_core/kernel/kernel.py`) — only execution path; plans, executes tools, generates
- **Provider Router** (`aura_core/llm/router.py`) — five routing policies (LOCAL_FIRST, CHEAPEST, FASTEST, QUALITY_FIRST, BALANCED) with circuit breakers and failover
- **Trust Channels** (SYSTEM/USER/DATA) — gate what content can do; retrieved knowledge on DATA channel with per-turn nonce

## Scheduled Tasks (Celery Beat)

7 tasks defined in `celery_app.py`:

| Task | Schedule | Queue |
|------|----------|-------|
| `generate-insights-hourly` | Every hour | realtime |
| `memory-consolidate-daily` | 02:30 daily | realtime |
| `drain-event-outbox` | Every 60s | realtime |
| `daily-briefing` | Every hour; each user is briefed once, 06:00–12:00 their local time | realtime |
| `reap-agent-workspaces` | Every 30 min | realtime |
| `drain-finetune-queue` | Every 120s | learning |
| `knowledge-refresh-nightly` | 02:00 daily (configurable via `AURA_LEARNING_REFRESH_CRON`) | learning |

## Database Migrations

Alembic with 5 migrations (`alembic/versions/0001_initial_schema.py` through `0005_github_identities.py`), single head at `0005`.

## System Dependencies (Production Docker Image)

- **Tesseract OCR** (`tesseract-ocr`) — Phase F1: scanned PDF OCR
- **Piper TTS** — Phase F2: self-hosted text-to-speech (ONNX models)
- **espeak-ng** — Phase F2: fallback TTS
- **faster-whisper** — Phase F2: self-hosted speech-to-text (CTranslate2)

## Running Tests

```bash
cd aura_backend
pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock   # hash-pinned (F-38)
pip install -r requirements-dev.txt                       # separately: unhashed

# Default suite (no Docker, no network, no model)
pytest

# Architecture fitness tests
pytest tests/architecture -q

# Legacy unittest suite (also gated in CI)
TESTING=True python -m unittest discover -p "test_*.py"

# Load tests (opt-in, needs real Postgres/Qdrant/Redis/Ollama)
pytest tests/load -m load -s

# Benchmarks (opt-in, wall-clock)
pytest tests/benchmarks -m bench
```

## Configuration

Environment variables (see `aura_core/config/settings.py`):

- `AURA_DATABASE_URL` — Postgres connection string
- `AURA_JWT_SECRET` — JWT signing secret (file-hydrated in production)
- `AURA_SANDBOX_ENABLED` — enable persistent engineer workspaces (opt-in)
- `AURA_ANALYSIS_TOOLS_ENABLED` — the 17 pure code/data analysis tools (default: true; ADR-036)
- `AURA_TOOLS_FILES_ROOT` / `AURA_TOOLS_HTTP_ALLOWED_HOSTS` — switch on the read-only `list_files`/`read_file` (confined to that directory) and the host-allowlisted `fetch_url`; both off while empty
- `AURA_WEB_SEARCH_URL` (+ `AURA_WEB_SEARCH_TIMEOUT_S`, `AURA_WEB_SEARCH_ENGINES`, `AURA_WEB_SEARCH_LANGUAGE`) — base URL of an operator-run SearXNG (JSON format enabled); switches on `web_search` (title/url/snippet/engine, untrusted content, request pinned to that host, no redirects). Off while empty; never a hosted search API (ADR-040)
- With `AURA_TOOLS_FILES_ROOT` set, the same confined directory also serves `ocr_image`, `inspect_spreadsheet`, `parse_document`, `analyze_image_requirements` and `transcribe_audio`; each additionally needs its engine (OCR, vision/OCR, faster-whisper; ffmpeg on `PATH` for video) and is simply not registered without it (ADR-040)
- `AURA_OPEN_EXEC_TOOLS_ENABLED` — `generate_and_run_tests` / `analyze_test_quality`, which run pytest in a child of the gateway; `engineer_agent` + confirmation, no container (default: false; ADR-036)
- `AURA_DESKTOP_OPS_TOOLS_ENABLED` — `docker_build` / `docker_compose` / `profile_python` on a paired desktop only (default: true; ADR-036)
- `AURA_ALLOW_REMOTE_INFERENCE` — gate remote inference (default: false)
- `AURA_LEARNING_REFRESH_CRON` — knowledge refresh schedule (default: `0 2 * * *`)
- `OLLAMA_NUM_PARALLEL` — model concurrency (default: 1)

## Self-Hosting Guarantee

External inference APIs are rejected at construction. The build fails if a hosted provider appears in:
- Python code (`tests/architecture/test_no_external_inference.py` layers 1-3)
- Flutter client dependencies (layer 4)
- Deployment manifests (layer 5)
- Provider config templates (layer 6)

## Deployment

Production target: 4 vCPU, 16 GB RAM, 100 GB SSD.

```bash
cd /opt/aura-stack/deploy
cp .env.example .env && $EDITOR .env      # set AURA_DOMAIN
./scripts/bootstrap-secrets.sh
docker compose -f docker-compose.prod.yml --env-file .env up -d
```

See [Deployment Guide](../docs/guides/deployment-guide.md) for full instructions.