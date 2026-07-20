
# AURA AI — Project Knowledge Base

*Compiled by full-repository analysis (code-level agents + direct file review + synthesis of 29 pre-existing project docs). Facts below are labeled **[VERIFIED]** when confirmed by direct code/config reading in this pass, or **[DOCUMENTED]** when sourced only from prior project reports that could not be cross-checked line-by-line against current code. Where prior docs contradict each other or the verified code, both are stated.*

---

## 1. Executive Summary

AURA AI is a **hybrid online/offline personal-AI-assistant application**: a Flutter client (Android/iOS/Windows/macOS/Linux/Web) paired with a Python FastAPI backend. It manages tasks, calendar, finances, health metrics, chat, document-based knowledge (RAG), Gmail/GitHub/Drive automations, and a continuous-learning feedback loop — all usable **offline** via an on-device LLM (bundled Qwen2‑1.5B GGUF run through `flutter_llama`/llama.cpp) and **online** via Gemini 2.5 Flash through the FastAPI backend, with automatic failover between the two. **[VERIFIED]**

The codebase shows two generations of prior work: an early "not production ready" audit baseline and a later "production ready" verification pass claiming the same defects were fixed. Direct code inspection in this pass confirms **some** claimed fixes are real (e.g., AES‑256‑GCM column encryption, deterministic Qdrant IDs, semantic intent routing) but also surfaces **new, currently-live issues** the docs don't mention (secrets committed to git, a permanent dev-login bypass in release builds, an orphaned `/automations` route, mocked device attestation client-side). Treat the project as **functional but not hardened for production** until the flagged risks (§19) are addressed. **[VERIFIED]**

---

## 2. Technology Stack

**Client (`aura_client/`, Flutter 3, Dart SDK ^3.11.4, version 3.0.1):**
- State: `flutter_riverpod` 3.3.1 — 100% manual `Notifier`/`AsyncNotifier` classes, no code-gen.
- Routing: `go_router` 17.3.0.
- Local DB: `drift` 2.33.0 (SQLite) with AES‑256‑GCM column encryption (`flutter_secure_storage` + PBKDF2 fallback).
- Cloud sync: `firebase_core` + `cloud_firestore` (no `firebase_auth`).
- Auth: `google_sign_in` 7.2.0, `local_auth` 2.3.0 (biometrics), `googleapis` (Calendar/Tasks/Drive).
- AI: `google_generative_ai` 0.4.7 (direct Gemini fallback), `flutter_llama` 0.1.0 (native on-device llama.cpp via FFI).
- UI: `google_fonts`, `fl_chart`, `flutter_markdown`, Material 3 glassmorphic dark theme.
**[VERIFIED]**

**Backend (`aura_backend/`, Python 3.11, FastAPI):**
- Web: `fastapi`, `uvicorn[standard]` (4 workers).
- AI: `google-genai` (Gemini 2.5 Flash), `sentence-transformers` (offline embeddings), custom centroid classifiers.
- Vector DB: `qdrant-client` (cloud or embedded local fallback).
- Background jobs: `celery` + `redis`.
- Auth: `firebase-admin`, `google-auth`, `google-api-python-client` (Play Integrity).
- Docs/files: `pypdf`.
**[VERIFIED]**

**Infra**: Docker (2-stage build), `docker-compose.yml` (backend + qdrant + redis + celery_worker), deployed to **Hugging Face Spaces** (confirmed by `aura_backend/README.md` HF YAML front-matter and the real production build command in `walkthrough.md`). Older `Deployment_info.txt` documents a **Railway** deployment; `Doc/DEPLOYMENT.md` documents a third, unused **Render/Vercel** topology — three inconsistent deployment narratives exist in the repo; HF Spaces is the one with corroborating live evidence. **[VERIFIED + DOCUMENTED contradiction]**

---

## 3. Architecture Overview

Layered **offline-first, hybrid client/server** architecture:

```mermaid
graph TD
    UI[Flutter UI - features/*] --> Router[AuraAIRouter]
    Router -->|online, DNS probe OK| OnlineMgr[OnlineAgentManager]
    Router -->|offline / stream error| OfflineMgr[OfflineAgentManager]
    OnlineMgr -->|POST /api/chat/agent| Backend[FastAPI agent_orchestrator]
    OnlineMgr -.direct SDK fallback.-> GeminiSDK[google_generative_ai]
    OfflineMgr --> NativeLlama[NativeLlamaInferenceProvider - flutter_llama/GGUF]
    Backend --> Gemini[Gemini 2.5 Flash]
    Backend --> Qdrant[(Qdrant: RAG + memory, single collection)]
    Backend --> Redis[(Redis: Celery broker + nonce store)]
    Backend -->|fallback, disabled if DEPLOYMENT_MODE=cloud| Ollama[Ollama local]
    UI --> Drift[(Drift/SQLite, AES-256-GCM columns)]
    UI --> SyncEngine[SyncEngine]
    SyncEngine --> Firestore[(Firestore users/{uid}/*)]
```

- **Client shell**: Riverpod providers colocated per-file; `AppDatabase` (Drift) is the source of truth for tasks/calendar/finance/health/chat/memories; `SyncEngine` does last-write-wins bidirectional sync to Firestore. **[VERIFIED]**
- **Routing decision**: `AuraAIRouter.selectRoute()` checks a manual override else a 2s DNS probe (`google.com`); `routePromptStream()` streams online and auto-fails-over to offline on any exception mid-stream. **[VERIFIED]**
- **Backend orchestration**: `agent.py`'s `execute_agent_stream()` is the real pipeline — intent classification (semantic centroid + keyword fallback) → sentiment-driven tone adaptation → memory + RAG context injection → optional Gemini function-calling tools (Drive search/ingest/RAG query) → Gemini streaming with retry → Ollama fallback (unless cloud mode) → mock response (debug only). `services/agents.py`'s `BaseAgent` class hierarchy is **dead code**, superseded by this inline implementation. **[VERIFIED]**
- **RAG + memory share one Qdrant collection**, partitioned by a `type`/`user_id` payload filter rather than separate collections — a design smell flagged by the backend agent. **[VERIFIED]**
- **Firestore is owned exclusively by the client** — the backend never touches Firestore at runtime (only a standalone, unimported `backfill_firestore.py` migration script does). **[VERIFIED]**

---

## 4. Project Structure

```
AURA AI/
├── aura_client/            Flutter app (client)
│   ├── lib/core/            ai/, config/, database/, routing/, security/, theme/
│   └── lib/features/        analytics, auth, automations, calendar, chat, finance,
│                             health, home, knowledge, learning, models, settings,
│                             sync, tasks   (13 feature modules)
├── aura_backend/           FastAPI backend (has its OWN nested .git — see §19)
│   ├── main.py               API surface (497 lines)
│   ├── services/              agent.py, agent_router.py, agents.py (dead), ai.py,
│   │                          automation.py, budget.py, deterministic_id.py, drive.py,
│   │                          learning_pipeline.py, memory.py, ollama.py, rag.py,
│   │                          rbac.py, security.py, sentiment.py
│   ├── celery_app.py, tasks.py
│   ├── local_qdrant_storage/, test_local_db/ (scratch artifact)
│   └── scratch/backfill_firestore.py (byte-identical duplicate of root-level copy)
├── Doc/, audit/             29 pre-existing analysis/report markdown files (see §18)
├── firestore.rules, storage.rules
├── docker-compose.yml
└── credentials.json, .env, Deployment_info.txt   ⚠ tracked in git, see §19
```
**[VERIFIED]**

---

## 5. Module / Feature Breakdown

| Feature | Purpose | Local data | Remote calls |
|---|---|---|---|
| **auth** | Google Sign-In + biometric cached login + dev bypass | `flutter_secure_storage` (id/email/token) | Google OAuth only — no Firebase Auth exchange |
| **home** | Aggregate dashboard | reads all tables | `ollamaHealthProvider` ping |
| **chat** | Core conversational UI (1458-line `chat_page.dart`, largest file) | `Messages` table | `/api/chat/agent`; parses trailing `[ACTIONS]` JSON to auto-create tasks/events/finance/health records |
| **tasks** | Task list w/ priority/category | `Tasks` | Google Tasks API bidirectional sync |
| **calendar** | Events | `CalendarEvents` | Google Calendar API (30-day window) |
| **finance** | Income/expense log | `FinanceRecords` | none (Firestore sync only) |
| **health** | Water/sleep/calories/exercise/weight | `HealthRecords` (per-day upsert) | none |
| **automations** | Gmail/GitHub/Drive AI triage | none local | `/api/automation/analyze-email`, `/api/automation/analyze-issue`, Gmail/GitHub/Drive REST APIs — **orphaned route, unreachable from any nav UI** |
| **knowledge** | RAG document ingestion/search | none local | `/api/rag/ingest`, `/api/rag/query` |
| **learning** | Continuous-learning dashboard | `LearningLogs` | `/api/learning/evaluate`, `/api/learning/build_dataset` |
| **models** | Offline GGUF model manager | `OfflineModelWeights` | direct Hugging Face file downloads |
| **analytics** | Telemetry charts | `TelemetryLogs` | none — **table is never written to client-side; page always shows empty state** |
| **sync** (engine only) | Firestore ↔ Drift LWW sync | all synced tables | Firestore `users/{uid}/{tasks,calendar,finance,health,memories}` |
**[VERIFIED]**

---

## 6. Business Rules & Requirements

- **Agent personas** (backend `agent.py`): `orchestrator`, `task_planner`, `finance_advisor`, `health_companion`, `academic_coach`, `research_agent`, `decision_agent`. Every persona's system prompt is appended with `ACTION_INSTRUCTIONS` directing the model to emit a trailing `[ACTIONS]` JSON array (5 allowed action types: create task/event/expense/income/health-log) that the client parses and executes locally. **[VERIFIED]**
- **Intent routing**: semantic nearest-centroid classification (`agent_router.py`, precomputed `routing_centroids.json`) with a keyword-cascade fallback if the router throws. **[VERIFIED]**
- **Sentiment-adaptive tone**: 5-class centroid sentiment classifier (`sentiment.py`) injects tone-adaptation text into the system prompt for Frustrated/Urgent/Negative/Positive states. **[VERIFIED]**
- **Memory extraction**: Gemini structured-output extraction with a 0.75 confidence threshold; debug-mode keyword fallback exists for tests. **[VERIFIED]**
- **RAG chunking**: 1000 chars/chunk, 100-char overlap, deterministic SHA-256-derived point IDs (idempotent re-ingestion). **[VERIFIED]**
- **Gemini cost control**: hard cap of 2 Gemini calls per request (`budget.py`, `contextvars`-scoped) — independent of RBAC. **[VERIFIED]**
- **RBAC tiers**: `User(0) < Premium(1) < Moderator(2) < Developer(3) < Admin(4) < SuperAdmin(5)`; only 3 endpoints enforce it (`/api/learning/export`, `/build_dataset` = Admin; `/evaluate` = Developer). The `roles.json` override file the code expects **does not exist**, so role resolution silently falls through to env-list or default `"User"`. **[VERIFIED]**
- **Offline model catalog**: gemma2:2b / phi3 / qwen2:1.5b / llama3:8b, hardcoded Hugging Face download URLs; Qwen2‑1.5B ships bundled in the app asset bundle as the guaranteed offline default. **[VERIFIED]**
- **Google OAuth scopes** (per `Doc/migration_guide.md`): `drive.readonly`, `drive.metadata.readonly` added to support PDF ingestion from Drive. **[DOCUMENTED]**

---

## 7. API Catalog

All protected endpoints require `Authorization: Bearer <token>` (Google ID token or Firebase ID token) via `verify_token`, plus `X-Device-Attestation`/`X-Device-Platform` headers checked before token verification. **[VERIFIED]**

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | none | liveness |
| `/api/chat/stream` | POST | token | non-agent Gemini streaming chat + RAG context injection |
| `/api/chat/agent` | POST | token | **primary chat endpoint** — full agent orchestration pipeline |
| `/api/rag/ingest` | POST | token | queues Celery `ingest_document_task` |
| `/api/rag/query` | POST | token | Qdrant similarity search |
| `/api/automation/analyze-email` | POST | token | Gemini-structured email triage |
| `/api/automation/analyze-issue` | POST | token | Gemini-structured GitHub issue breakdown |
| `/api/memory/extract` | POST | token | extract + save facts from a conversation |
| `/api/memory/save` | POST | token | save a single fact |
| `/api/memory/list` | GET | token | list all memories |
| `/api/memory/delete/{point_id}` | DELETE | token | delete a memory |
| `/api/telemetry/log` | POST | token | append to local `telemetry_logs.jsonl` (non-durable on ephemeral hosting) |
| `/api/learning/log` | POST | token | append feedback record |
| `/api/learning/export` | GET | **Admin** | LoRA dataset export (Alpaca/ChatML/OpenAI format) |
| `/api/learning/build_dataset` | POST | **Admin** | async via Celery, writes `train_lora.json` |
| `/api/learning/evaluate` | GET | **Developer** | feedback dataset metrics |
| `/api/tags`, `/api/chat`, `/api/pull`, `/api/delete` | GET/POST | **none** | raw Ollama proxy passthrough — unauthenticated by design; only `/tags` and `/delete` were hardened to return clean 503s, `/chat` and `/pull` still leak raw exception text into the stream body |

Not implemented despite being referenced in older docs: a documented `/api/v2/...` route family (aspirational, never enumerated); `/api/rag/ingest_pdf` exists in code but was undocumented in `Doc/api_documentation.md`. **[VERIFIED + DOCUMENTED gap]**

---

## 8. Database Overview

### Client — Drift/SQLite (`aura_client/lib/core/database/database.dart`, schema v6, 11 tables)

| Table | Encrypted columns (AES-256-GCM) |
|---|---|
| Tasks | title, description |
| CalendarEvents | title, description |
| FinanceRecords | description |
| HealthRecords | — |
| Messages | content |
| AgentConfigurations | — |
| UserMemories | fact |
| PendingDeletes | tombstone log for sync |
| LearningLogs | prompt, response |
| OfflineModelWeights | — |
| TelemetryLogs | — (and never populated, see §17) |

Encryption key: `flutter_secure_storage` (Keystore/Keychain/DPAPI) with a legacy-plaintext-file migration path and a PBKDF2-HMAC-SHA256 (10k iter) fallback if secure storage fails, and a last-resort static hardcoded key string for decrypting old CBC-format data. On boot, `beforeOpen` runs an integrity check and **wipes all local tables** if decryption fails on sampled rows (e.g., after a backup restore with a lost key). **[VERIFIED]**

### Backend — Qdrant (vector)
One collection (`aura_knowledge` cloud / `aura_knowledge_local` embedded fallback), 768d Cosine (or 384d if only local embeddings available), holding **both** RAG chunks and user memories, partitioned by `type`/`user_id` payload filters with keyword indexes. Auto-recreates the collection on dimension mismatch — **silent destructive data loss risk**. A separate `test_local_db/` (4-dim) is an unreferenced test artifact shipped in the Docker image. **[VERIFIED]**

### Firestore (client-owned, current target structure)
```
users/{uid}/tasks/{id}
users/{uid}/calendar/{id}
users/{uid}/finance/{id}
users/{uid}/health/{id}
users/{uid}/memories/{id}
config/{...}          (read-only to authenticated users)
```
Legacy root-level collections (`/tasks`, `/calendar_events`, etc.) are documented as deprecated and incompatible with current security rules; `backfill_firestore.py` is meant to migrate them but its existence is **disputed between two prior docs** (one says implemented, one says documented-only) — confirmed present in this pass at `aura_backend/backfill_firestore.py` and duplicated at `aura_backend/scratch/backfill_firestore.py`, but it is **never imported or invoked by any running service** — a standalone manual-run script only. **[VERIFIED]**

### Flat-file stores (backend, non-durable on ephemeral hosting)
`learning_logs.json`, `routing_centroids.json`, `sentiment_centroids.json`, `telemetry_logs.jsonl`, `train_lora.json`. **[VERIFIED]**

No relational/SQL database exists on the backend. **[VERIFIED]**

---

## 9. State Management Overview

100% manual Riverpod, no `riverpod_generator`. Pattern: `class XNotifier extends Notifier<T>` + `final xProvider = NotifierProvider<XNotifier, T>(...)`, colocated with the class in the same file (no central `providers.dart`). `StreamProvider.family.autoDispose` wraps Drift `.watch()` queries; `FutureProvider.autoDispose` wraps one-shot backend calls. One legacy `StateProvider` usage remains (`local_model_seeder.dart`). `main.dart` builds a single root `ProviderContainer` wrapped in `UncontrolledProviderScope`. **[VERIFIED]**

---

## 10. Navigation Flow

```
/login (standalone, no shell)
  └─ auth success → /home

ShellRoute (adaptive bottom-nav <768px / NavigationRail ≥768px)
  Primary (6 tabs): /home /tasks /calendar /finance /health /chat
  Secondary (via Settings grid only): /settings → /profile /models /learning /knowledge /analytics
  Orphaned: /automations — registered in router.dart but reachable from NO in-app UI
            (not in nav, not in Settings grid, not linked from Home)
```
**[VERIFIED]**

---

## 11. Authentication & Authorization Flow

**Client**: App boot → `EncryptionService.init()` → best-effort `Firebase.initializeApp()` → fire-and-forget model seeding. `AuthNotifier` attempts silent Google restore (always returns null — explicit re-auth required). Login page checks cached credentials + biometric availability; if both present, biometric unlock alone restores the cached session **without re-contacting Google or Firebase**. Google Sign-In path stores id/email/name/photo/idToken in secure storage; **the Google idToken is used only as a bearer token to the backend and for attestation headers — there is no Firebase Auth credential exchange**, and `firebase_auth` is not even a dependency. Consequently `SyncEngine`'s Firestore calls run with no authenticated Firebase session, even though `firestore.rules` gates all access on `request.auth != null` — meaning either Firestore writes are silently failing/being rejected in the current build, or there's an undocumented mechanism not found in this pass. **This is the single most important open question for a senior architect to resolve before relying on cloud sync.** A permanent "Developer / Offline Access" bypass (`signInMock`) creates a `mock_user_123` session and ships in release builds with no debug-mode guard. **[VERIFIED]**

**Backend**: `verify_token` dependency — device attestation header check first (Android: real Play Integrity API call with an unsigned-fallback JWS decode path; iOS: placeholder substring check only, not real AppAttest) → dual-accepts Google ID token or Firebase ID token → debug/testing-mode mock user fallback. RBAC (`rbac.py`) layered on top for 3 admin/dev endpoints only. Both device-attestation mock acceptance and full auth bypass are gated by `debug_mode`/`TESTING`/`allow_mock_attestation` settings — **all default to permissive** (`allow_mock_attestation: true` by default in `config.py`). **[VERIFIED]**

---

## 12. Data Flow (major workflows)

**Online chat** — User types in `chat_page.dart` → `ClientAgentOrchestrator`/direct call → `AuraAIRouter` (DNS probe passes) → `OnlineAgentManager` POST `/api/chat/agent` (+ attestation headers, + Google Drive token if available) → backend `agent_orchestrator.execute_agent_stream`: intent classify → sentiment → memory/RAG context injection → optional Drive/RAG tool calls → Gemini stream → client parses trailing `[ACTIONS]` JSON → `chat_page.dart` writes new rows into `Tasks`/`CalendarEvents`/`FinanceRecords`/`HealthRecords` via their repositories → UI updates via Riverpod stream providers watching Drift. **[VERIFIED]**

**Offline chat** — Same entry point, DNS probe fails or online stream throws → `OfflineAgentManager` → `NativeLlamaInferenceProvider` resolves the active model from `OfflineModelWeights`, loads GGUF via `flutter_llama` FFI, streams tokens locally, no backend/network involved; `ClientAgentOrchestrator` pre-injects memory + RAG context client-side in this path only (comment: "backend handles online" RAG/memory itself). **[VERIFIED]**

**RAG ingestion** — Knowledge Center UI → `/api/rag/ingest` → Celery `ingest_document_task.delay` (Redis broker) → worker chunks text (1000/100 overlap) → embeds (Gemini → local MiniLM in debug only → Ollama → zero-vector mock) → deterministic-ID upsert into Qdrant. **[VERIFIED]**

**Cloud sync** — `SyncEngine` (triggered from a chat-page sync button + 60s timer) performs per-table last-write-wins diff between Drift and Firestore `users/{uid}/*` subcollections — see the auth gap noted in §11 regarding whether this is actually authorized. **[VERIFIED]**

---

## 13. Dependency Graph (textual)

```
aura_client → (HTTP) → aura_backend/api  [primary]
aura_client → Firebase (Firestore, Storage)              [direct, no Auth session — see §11]
aura_client → Google APIs (Calendar, Tasks, Drive, Gmail; OAuth via google_sign_in)
aura_client → GitHub REST API (PAT stored in secure storage)
aura_client → Hugging Face (direct GGUF file downloads)
aura_client → flutter_llama (native FFI, on-device inference)
aura_client → Ollama (implemented, code path unreachable — dead)

aura_backend → Gemini API (google-genai)
aura_backend → Qdrant (cloud or embedded local fallback)
aura_backend → Redis (Celery broker/backend + attestation nonce store)
aura_backend → Firebase Admin (token verification only, not Firestore data)
aura_backend → Google Play Integrity API (Android attestation)
aura_backend → Google Drive REST API (raw httpx, user's OAuth token)
aura_backend → Ollama (local fallback, disabled when DEPLOYMENT_MODE=cloud)
```
**[VERIFIED]**

---

## 14. Shared Components / Reusable Services

- `ShellLayout` (adaptive nav shell), `AuraTheme` (dark glassmorphic Material 3).
- `EncryptedTextConverter` (Drift `TypeConverter` for AES-256-GCM columns).
- `DeviceValidationService` (client-side attestation header generator — **currently fully mocked**).
- `InferenceProvider` interface (decouples chat UI from Ollama vs native-llama backends).
- `deterministic_id.py`/`deterministic_id.dart` (stable SHA-256-derived IDs, shared concept both sides).
- Backend: singleton services pattern (`rag_service`, `memory_service`, `agent_orchestrator`, `sentiment_analyzer`, `agent_router`, `learning_pipeline`, `ollama_service`) — all module-level singletons, no DI container.
**[VERIFIED]**

---

## 15. Configuration Overview

**Backend `config.py`** (Pydantic `BaseSettings`, `.env`-backed): `port`, `gemini_api_key`, `qdrant_host`/`qdrant_api_key`, `ollama_host`/`ollama_model`, `local_embedding_model`, `allowed_origins`, `google_client_id`, `firebase_service_account_path`/`firebase_credentials_json`, `debug_mode`, `allow_mock_attestation` (defaults **true**). Additional untyped env vars read ad hoc elsewhere: `REDIS_HOST`, `TESTING`, `DEPLOYMENT_MODE`, `ADMIN_EMAILS`/`DEVELOPER_EMAILS`. **[VERIFIED]**

**docker-compose.yml** sets `REDIS_URL`/`CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`, none of which `celery_app.py` actually reads (it only reads `REDIS_HOST`, which compose never sets) — **likely a live bug**: the compose-orchestrated Celery worker/backend would default `REDIS_HOST` to `localhost` inside its own container rather than resolving the `redis` service by Docker DNS name. **[VERIFIED]**

**Client**: single `AppConfig.backendUrl`, overridable at build time via `--dart-define=BACKEND_URL=...`. **[VERIFIED]**

---

## 16. External Integrations

| Integration | Used for | Notes |
|---|---|---|
| Google Gemini API (2.5 Flash) | primary online LLM, embeddings, structured extraction | budget-capped 2 calls/request |
| Qdrant (Cloud or embedded local) | RAG + memory vector store | single shared collection |
| Redis | Celery broker/backend, attestation nonce store | in-process fallback if unreachable |
| Firebase (Firestore + Storage, Admin SDK) | client cloud sync + backend token verification | no Firebase Auth session — see §11 |
| Google Sign-In / OAuth | login, Calendar/Tasks/Drive/Gmail scopes | idToken doubles as backend bearer token |
| Google Play Integrity API | Android device attestation | real crypto verification, with an unsigned fallback path |
| GitHub REST API | issue-triage automation | user PAT stored client-side |
| Hugging Face | GGUF model downloads + backend hosting (Spaces) | |
| Ollama (optional, local) | offline fallback model host | client path is dead code; backend path disabled in cloud mode |
**[VERIFIED]**

---

## 17. Technical Debt Summary

**Dead / unreachable code [VERIFIED]:**
- `OllamaInferenceProvider` (client) — fully implemented, never wired (`activeInferenceProvider` hardcoded to native llama).
- `services/agents.py` (backend `BaseAgent` hierarchy) — superseded by `agent.py`, not imported anywhere.
- `rbac.py:require_role_decorator` — unused alternative to the real `require_role` dependency.
- `agent_router.py:evaluate_router()`, all of `sentiment.py`'s evaluation path — no API endpoint exposes them.
- `/automations` route — registered but unreachable from any UI.
- `AppDatabase.insertTelemetry()` — never called; `TelemetryLogs` table (and the Analytics page) is permanently empty.
- `backfill_firestore.py` duplicated verbatim at `scratch/backfill_firestore.py`; neither is imported/invoked by running code.
- `test_local_db/` — unreferenced Qdrant test artifact, ships in the Docker image (not `.dockerignore`'d).
- Likely-broken call: `FeedbackCollector.deleteLog()` references `_db.db.learningLogs`, a member that doesn't appear to exist on `AppDatabase` — probable copy-paste bug.

**Duplication / large files [VERIFIED]:**
- `sync_engine.dart` (577 lines) — near-identical copy-pasted LWW sync logic per table; strong generic-refactor candidate.
- `chat_page.dart` (1458 lines), `automations_page.dart` (898), `tasks_page.dart` (808), `calendar_page.dart` (788), `finance_page.dart` (770) — all large, feature-complete but monolithic presentation files.

**Inconsistent hardening [VERIFIED]:**
- Ollama proxy endpoints: `/api/tags`/`/api/delete` correctly return 503 JSON on failure; `/api/chat`/`/api/pull` still leak raw exception text into the stream body.

**Config drift [VERIFIED]:** `docker-compose.yml` sets Celery/Redis env vars that `celery_app.py` doesn't consume, and never sets the one it does (`REDIS_HOST`) to the correct Docker service name.

**RBAC gap [VERIFIED]:** `roles.json` override file referenced in code doesn't exist in the repo — that resolution tier is currently a no-op.

---

## 18. Prior-Documentation Landscape (meta)

The repo carries 29 pre-existing analysis markdown files across two contradictory generations:
- **Gen‑1 ("audit/problem")**: `audit/*.md`, `health_report.md`, `technical_debt_report.md`, `Doc/aura_ai_requirements_alignment_report.md` — score the system **58/100, "Not Production Ready,"** listing critical bugs (plaintext crypto key, broken Firestore paths, Windows filename crash, no RBAC, fake attestation, no Celery/Redis).
- **Gen‑2 ("verification/fixed")**: `architecture_verification.md`, `performance_verification.md`, `security_verification.md`, `verification_report.md`, `implementation-log.md`, `security-analysis.md`, `system-analysis.md`, root `migration-plan.md`/`refactor-plan.md`/`risk-assessment.md`, `release_notes_v2.0.0.md` — claim the same items were fixed, scoring **99.5/100, "Production Ready."**

This pass's direct code reading confirms several Gen‑2 claims are real (GCM encryption, deterministic IDs, semantic routing, Celery/Redis do exist) but also found **defects neither generation of docs mentions** (git-committed secrets, the release-build dev-login bypass, the orphaned automations route, the empty telemetry table, the Firebase-Auth/Firestore-rules mismatch). **Do not trust either doc generation's overall health score — verify against code per-feature.** Full contradiction list preserved from the synthesis pass:
1. Celery/Redis existence — disputed, **resolved: they exist** (confirmed in `celery_app.py`, `tasks.py`, `docker-compose.yml`).
2. `backfill_firestore.py` existence — disputed, **resolved: it exists** but is unused/orphaned.
3. Overall production-readiness score (58 vs 99.5) — **neither is accurate**; real state is "functional, several unresolved security gaps."
4. Testing coverage (40/100 vs 100/100) — **resolved: real test files exist** (`test_agents.py`, `test_security.py`, 4 Flutter test files) covering auth/rate-limit/attestation/RBAC/mock-mode contracts well, but **zero coverage** for `rag.py`, `memory.py`, `automation.py`, `drive.py`, `learning_pipeline.py`, or Celery tasks.
5. Embedding model name (`gemini-embedding-001` vs `text-embedding-004`) — **resolved: code uses `gemini-embedding-001`.**
6. Deployment host (Render/Railway vs Hugging Face Spaces) — **resolved: HF Spaces is live**; Railway info is a stale snapshot; Render/Vercel guide is unused.
7. Kubernetes blue/green rollback claim (`risk-assessment.md`) — no K8s deployment exists anywhere in the repo; **stale/aspirational boilerplate**.

---

## 19. Identified Risks (this pass, verified)

| Severity | Risk | Evidence |
|---|---|---|
| **Critical** | Secrets committed to git with **no `.gitignore` anywhere in the repo**: `credentials.json` (Firebase service account) at both root and `aura_backend/`, `aura_backend/.env`, `aura_backend/Deployment_info.txt` (contains plaintext Qdrant API key and Gemini API key) | `git ls-files` confirms all four are tracked |
| **High** | Permanent "Developer / Offline Access" login bypass ships in release builds, no `kDebugMode` guard, pre-filled with a real-looking account | `login_page.dart:216-266`, `auth_provider.dart:132` (`signInMock`) |
| **High** | Client-side device attestation is entirely mocked (hardcoded token strings); iOS server-side attestation is a placeholder substring check, not real AppAttest; Android fallback path (when Play Integrity API is unreachable) trusts an **unsigned** self-decoded JWS | `device_validation_service.dart`; `security.py:124-139, 166-176` |
| **High** | No Firebase Auth session backs the client's Firestore reads/writes, yet `firestore.rules`/`storage.rules` gate everything on `request.auth != null` — sync may be silently broken or relying on an undocumented mechanism | `auth_provider.dart` (no `firebase_auth` dependency), `firestore.rules` |
| **Medium** | Rate limiting and attestation-nonce cache are in-process state, not shared across the Dockerfile's 4 uvicorn workers (nor across horizontal replicas) — effective limits are inflated/inconsistent | `Dockerfile:44` (`--workers 4`), `RateLimitMiddleware`, `security.py` local nonce cache |
| **Medium** | Local-disk-only data stores (`telemetry_logs.jsonl`, `learning_logs.json`, `train_lora.json`) have no volume mount guarantee — data loss on redeploy under ephemeral hosting (HF Spaces/Railway) | `main.py`, `tasks.py`, Dockerfile/compose review |
| **Medium** | `docker-compose.yml`'s Celery/Redis env vars don't match what `celery_app.py` reads — local-compose Celery workers likely can't reach Redis by hostname as configured | `docker-compose.yml` vs `celery_app.py:4` |
| **Low** | `allow_mock_attestation` defaults to `true` and `roles.json` RBAC override is a no-op (missing file) — both fail open rather than closed | `config.py`, `rbac.py` |
| **Low** | `aura_backend` has its own nested `.git` — possible accidental embedded/orphaned repo rather than a proper submodule, risking confusing/incomplete history | direct `ls` observation in this pass |

---

## 20. Areas Requiring Future Improvement

- Resolve the Firebase-Auth/Firestore-rules mismatch (§11, §19) — either add a real Firebase Auth exchange or relax rules deliberately and document why.
- Remove or `.gitignore` + rotate all committed secrets (§19) before any further deployment.
- Gate the dev-login bypass behind `kDebugMode`/a build flavor.
- Decide the canonical deployment target and delete the two stale deployment guides, or update them.
- Wire or remove: `OllamaInferenceProvider` (client), `services/agents.py` (backend), the `/automations` route, telemetry writing.
- Move rate-limit/nonce state to Redis (already available) so it's correct across the 4 uvicorn workers and any horizontal scaling.
- Add test coverage for `rag.py`, `memory.py`, `automation.py`, `drive.py`, `learning_pipeline.py`, Celery tasks.
- Consolidate `sync_engine.dart`'s five near-identical per-table sync blocks into one generic function.
- Fix the compose file's Celery/Redis env var mismatch.
- Implement real iOS App Attest/DeviceCheck verification server-side, and remove the unsigned JWS fallback (or make it fail closed).

---

## 21. Overall Understanding Assessment

**~90% confident** in the architecture, feature set, API surface, database schema, security model, and data flows described above — all core claims in this document were confirmed by direct code reading (not just prior docs) via three independent exploration passes plus manual verification of git-tracked files, infra config, and Firebase rules.

**Remaining ~10% uncertainty:**
- Which deployment target is *actually* serving production traffic right now, and whether the Railway/Render artifacts are fully dead or partially still live.
- Whether the Firebase-Auth/Firestore-rules gap (§11/§19) is a live production bug or mitigated by something outside this repo (e.g., permissive rules deployed separately from what's checked in, or a server-side token-exchange step not present in the client code).
- Runtime behavior was not observed — no build/run/manual test was performed in this pass (out of scope per task constraints); all findings are static-analysis based.
- The nested `aura_backend/.git` situation (submodule vs. accidental embedded repo) was observed but not fully investigated (remotes, `.gitmodules` presence).

This level of understanding is sufficient to plan new feature work or targeted fixes without a further discovery phase, **except** for the Firebase Auth question in §11, which should be resolved first since it may mean the sync feature is currently non-functional in production.
