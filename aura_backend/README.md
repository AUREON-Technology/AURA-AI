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
- `AURA_LOOP_STUCK_DETECTION_ENABLED` / `AURA_LOOP_STUCK_IDENTICAL_CALLS` / `AURA_LOOP_STUCK_SAME_ERROR` / `AURA_LOOP_STUCK_NO_PROGRESS` — stop an agent loop that is repeating itself (default: true; 3 / 3 / 4; `0` disables a rule; ADR-037)
- `AURA_LOOP_RECOVERY_POLICY_ENABLED` / `AURA_LOOP_RECOVERY_BACKOFF_S` — retry, replan, surface or stop by failure kind instead of retrying every failed read (default: false; 0.5 s; ADR-037)
- `AURA_LOOP_TEST_SIGNALS_ENABLED` — a failing test run or non-zero command exit makes an agent pass "not done" and drives a replan (default: false; ADR-037)
- `AURA_CRITIC_ENABLED` / `AURA_CRITIC_LLM_ENABLED` — check the Engineer's plan before it runs and its answer against the tool results; the model critic is advisory (default: true / false; ADR-037)
- `AURA_ENGINEER_TOKEN_BUDGET` / `AURA_CONTEXT_PRESSURE_THRESHOLD` — the Engineer run's token budget (`0` = unbounded) and the fraction at which `context_pressure` is signalled (default: 400000; 0.8; ADR-037)
- `AURA_MEMORY_AUTO_CHECKPOINT_EVERY_TURNS` — write a structured session checkpoint (decisions / completed / blockers / next steps) in the background every N persisted turns; `0` = never (default: 0; ADR-038). `save_session_checkpoint` / `recall_session_checkpoint` and `GET`/`POST /api/v3/sessions/{id}/checkpoint` work regardless
- `AURA_REASONING_BACKGROUND_DELEGATION` — `delegate_to_agent(background=true)` returns an `agent_task_id` and runs the sub-turn as an asyncio task; adds `list_agent_tasks` / `get_agent_task` / `cancel_agent_task` (default: false; ADR-038). Tune with `AURA_REASONING_AGENT_TASKS_PER_USER` (3; counted in the shared cache), `AURA_REASONING_AGENT_TASKS_GLOBAL` (16; per process), `AURA_REASONING_AGENT_TASK_TTL_S` (3600) and `AURA_REASONING_AGENT_TASK_DEADLINE_S` (900). **Status and results are in the cache port, readable from any worker; the running task lives only in the process that started it and does not survive a restart** (a task whose worker died reads as `failed: lost` once its deadline has passed)
- `AURA_REASONING_STRUCTURED_HANDOFF` — a delegated sub-turn also gets the parent session's checkpoint (goal, decisions, finished, blockers, pending, relevant files) as one fenced DATA-channel block, and its result is stored back as a structured outcome read with `get_handoff` / `list_handoffs` (default: false; ADR-038)
- `AURA_REASONING_DEFAULT_PLAN_MODE` — what `/chat` does when a request names no `plan_mode` and no `plan`: empty keeps today's behaviour; `auto`, `plan`, `fast` or `review` (default: empty; ADR-038). `/chat` and `/chat/stream` accept `plan_mode` per request; the response and the stream's `done` frame carry `plan_mode` (`requested`, `effective`, `reason`, `planned`, `awaiting_approval`)
- `AURA_AUTO_LESSONS_ENABLED` — after an Engineer turn, derive candidate lessons (decisions, corrected errors, stated conventions) and queue them as `auto_lesson` memories in a pending scope no persona recalls; the owner approves or rejects them at `/api/v3/memory/lessons` (default: false; ADR-038, ADR-008)
- `AURA_TOOL_POLICY_FILE` — JSON/YAML file of declarative tool-call rules (deny / require confirmation by tool name and argument pattern, scoped by persona and sensitivity); validated at start-up, can only tighten the policy (default: none; ADR-039)
- `AURA_AUTONOMY_ALLOW_AUTONOMOUS` / `AURA_AUTONOMY_DEFAULT` — whether users may pick the `autonomous` review profile, and the default (`strict`|`standard`) for users who picked none; the profile is read and set through `GET`/`PUT /api/v3/engineering/policy` (default: off / `standard`; ADR-039)
- `AURA_SAFETY_REDACT_CONTEXT_SECRETS` / `AURA_SAFETY_REDACT_INPUT_SECRETS` / `AURA_SAFETY_REDACT_HISTORY_SECRETS` — redact credential-shaped text from documents, memories and loaded history; from the user's own message; and from persisted session history (default: all off; ADR-039)
- `AURA_SANDBOX_ALLOWED_EGRESS_HOSTS` — extra bare host names the workspace proxy may reach, added to `deploy/sandbox/filter` by `deploy/scripts/render-sandbox-filter.sh`; wildcards, URLs, ports and IP addresses are refused (default: none; ADR-039)
- `POST /api/v3/agents/stop-all` — the kill switch: cancels the caller's in-flight turns, closes their paired-desktop connection, and with `{"revoke_devices": true}` revokes their paired daemons (ADR-039)
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