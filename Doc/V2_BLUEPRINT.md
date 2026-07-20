# AURA AI — Version 2 Blueprint

*Written as the founding architecture/product document for a ground-up rebuild. V1 (documented in [PROJECT_KNOWLEDGE_BASE.md](PROJECT_KNOWLEDGE_BASE.md)) is used only as the source of business logic and feature inventory — every implementation decision below is made fresh. Each call states the alternative considered and why it lost.*

---

## 0. Verdict on V1, in one paragraph

V1 is a feature-complete but architecturally fragmented personal-assistant app: five unrelated domain silos (tasks/calendar/finance/health/chat) bolted onto a Settings page that's really a grid of sub-apps, a client-only database that the backend never sees (so no server-side automation is possible), a hand-rolled 577-line sync engine duplicated five times, and a chat UI that communicates tool calls via a magic `[ACTIONS]` string suffix instead of structured events. The AI is genuinely good (hybrid online/offline routing, on-device inference, RAG, sentiment/intent centroids) — that stays. Everything around it — data model, sync, IA, visual design, API shape — gets rebuilt.

---

## 1. Product Vision V2

**V1's implicit vision:** "a phone with five apps on it, plus a chatbot."
**V2's vision:** **AURA is a single AI surface that manages your life for you — you talk to it, it acts.** Tasks, calendar, finance, and health are not separate apps the user navigates between; they are *facts AURA knows about you* that surface contextually and that AURA can act on. The chat/copilot is not one tab among six — it is always one tap away from anywhere (a persistent capture affordance, like Spotlight or Linear's Cmd+K), because the fastest way to add a task is never "open Tasks → tap + → fill a form."

**Design principle:** every screen answers one of two questions — *"what does AURA think I should know right now?"* (Home/Insights) or *"let me look at my data directly"* (Life Hub, for when the user wants to browse/audit rather than converse). Nothing else.

**Cut from V1:** the flat Settings grid of 5 unrelated dashboards (Profile/Models/Learning/Knowledge/Analytics/Automations) — none of these are things a user opens repeatedly; they become a single "Account & System" area with clear sub-navigation, not six equally-weighted top-level cards.

**Added in V2:** a proactive Insights feed (AURA tells you things before you ask), a unified command palette, a notification center — see §8.

---

## 2. UX Audit of V1 (findings, not opinions — each is cited against the codebase)

| # | Finding | Evidence |
|---|---|---|
| 1 | No onboarding. New users land straight on a login screen, then an empty dashboard with zero guidance. | `login_page.dart`, `home_page.dart` — no first-run state anywhere |
| 2 | Settings page is a flat grid of 5-6 unrelated full-screen dashboards, not "settings" | `settings_page.dart` — Profile/Models/Learning/Knowledge/Analytics all one tap from a page called "Settings" |
| 3 | No global search or command palette; every action requires knowing which of 6 tabs it lives in | confirmed absent from `router.dart` route list and every feature page |
| 4 | Chat is the most-used surface (per architecture, it's the AI entry point) but is buried as tab 6 of 6, not the default/persistent affordance | `router.dart` `ShellLayout` nav order |
| 5 | Tool-call results communicated via a hand-parsed `[ACTIONS]` text suffix the model must format correctly — brittle, unexplainable to the user, no confirmation step before auto-creating a task/expense | `services/agent.py` `ACTION_INSTRUCTIONS`, `chat_page.dart` action-parsing |
| 6 | No notification center despite three features that generate events worth surfacing (automations, learning feedback, telemetry/failover) | absent from routes/nav |
| 7 | Finance/Health/Tasks/Calendar have zero cross-awareness — logging a $200 vet bill doesn't touch a health record; a task due tomorrow doesn't check calendar density | each feature reads/writes only its own Drift table |
| 8 | No accessibility semantics, no contrast audit, no reduced-motion handling, dark-mode-only (no light theme) | `theme.dart` — single hardcoded `AuraTheme`, no `Brightness` branching |
| 9 | No localization — every string is inline English in widget trees | grep confirms zero `intl`/ARB usage despite `intl` being a dependency |
| 10 | `/automations` was literally unreachable from any UI in V1 (fixed defensively in the hardening pass, but the fact it shipped hidden for presumably multiple releases shows IA wasn't being reviewed) | `router.dart` vs nav/Settings grid |

**Root cause behind #2, #3, #6, #7:** V1 was built feature-by-feature with no unifying information architecture pass. V2 fixes this at the IA layer (§3), not by polishing individual screens.

---

## 3. Information Architecture V2

```
Bottom nav / rail (4 destinations, down from 6):
  ⌂ Today       — AI-curated daily view: insights, what's due, quick capture
  ✦ Copilot     — full chat/agent surface (was buried at position 6, now position 2)
  ▤ Life Hub    — Tasks · Calendar · Finance · Health as filterable views of one
                   unified timeline/data model, not four separate pages
  ⚙ You         — Account & System: Profile, Models, Learning, Knowledge,
                   Analytics, Automations — organized as a settings LIST
                   (iOS Settings-app pattern), not a grid of dashboards

Global, reachable from anywhere:
  ⌘K / long-press FAB → Command Palette (search + quick actions + navigate)
  🔔 Notification Center (automations results, insights, learning prompts)
  + Floating capture button — opens Copilot pre-focused, from any screen
```

**Why merge Tasks/Calendar/Finance/Health into "Life Hub" instead of 4 tabs:** these are the same underlying concept (a dated, categorized record) with different schemas. Users don't think "let me open my Finance app" — they think "what's going on this week." Life Hub defaults to a unified chronological/agenda view with a type filter chip row, and only drills into type-specific detail (e.g., a finance-specific monthly chart) on demand. This directly fixes UX finding #7: cross-domain correlation becomes possible because it's one screen, one query, one AI context — not four isolated repositories.

**Why "You" is a list, not a grid:** grids of equally-sized cards imply equal frequency of use. In reality Profile is opened once a month, Models/Learning are power-user features, Analytics is occasional. A settings list (like iOS/Android system settings) with clear hierarchy communicates that correctly and scales to more items without redesign.

---

## 4. Design System V2

**Decision: keep Flutter + Material 3 as the base, but stop using default M3 components uncustomized, and add a real design-token layer.** (Rejected: Cupertino-only or fully custom render — too much cost for no product benefit; rejected: a second UI kit like Fluent — fragments the codebase.)

- **Tokens as data, not scattered hex codes.** `lib/core/design_system/tokens.dart` defines `AuraColors`, `AuraSpacing` (4/8/12/16/24/32/48), `AuraRadius`, `AuraElevation`, `AuraMotion` (duration/curve pairs: `fast=120ms`, `standard=200ms`, `emphasized=400ms`, all `Curves.easeOutCubic`/`Curves.easeInOutCubicEmphasized`), `AuraTypography` (Material 3 type scale: display/headline/title/body/label, each with a defined font, weight, and line-height — not ad hoc `TextStyle(fontSize: 14)` calls scattered through 30 files as in V1).
- **Light AND dark are both first-class**, generated from the same token seed (V1 was dark-only, hardcoded). Implemented via `ColorScheme.fromSeed` seeded from the brand indigo, with manually-tuned surface/container roles for the glassmorphic card look V1 already had good taste on — that visual language (glass cards, indigo/violet gradient accents) is *kept*, just formalized as reusable `AuraCard`, `AuraGradientContainer` widgets instead of copy-pasted `BoxDecoration`s in every page (V1 duplicates the same `BoxDecoration` construction in `chat_page.dart`, `home_page.dart`, `login_page.dart`, `settings_page.dart` independently).
- **Every color pair contrast-checked WCAG AA (4.5:1 text, 3:1 large text/UI)** as a CI-enforced rule (a small Dart script asserts contrast ratios of the token file at build time — cheap, prevents regression).
- **Component library**: `AuraButton` (primary/secondary/ghost/destructive variants), `AuraCard`, `AuraTextField` (with built-in validation state), `AuraEmptyState`, `AuraSkeletonLoader` (V1 uses raw `CircularProgressIndicator` everywhere — no skeletons, no shimmer), `AuraSnackbar` (success/error/info variants), `AuraDialog`. One definition, used everywhere, instead of the current pattern of every page building its own cards/buttons inline.
- **Motion**: shared-element transitions between Life Hub list items and their detail view (`Hero`), skeleton-loader-first instead of spinner-first, staggered list-item fade-in on first load (subtle, ≤150ms per item, respects `MediaQuery.disableAnimations`).
- **Accessibility built into the components, not bolted on**: every `AuraButton`/`AuraCard` requires a `semanticLabel`, text respects `MediaQuery.textScaler`, minimum 44×44 tap targets enforced by the component (V1 has several icon-only buttons with no semantic label and small tap targets, e.g., the chevron rows in `settings_page.dart`).

---

## 5. Mobile Architecture V2

**Decision: keep Flutter, keep Riverpod, but adopt `riverpod_generator` and real Clean Architecture per feature.** (Rejected: BLoC — team already invested in Riverpod, no functional gap that justifies a rewrite; rejected: switching to GetX/Provider — strictly worse tooling than Riverpod for this app's complexity.)

```
lib/features/<feature>/
  domain/        — entities (plain Dart classes, no Drift/Firestore types leaking in),
                   repository interfaces, use cases (single-method classes: AddTask, CompleteTask)
  data/          — DriftLocalDataSource, ApiRemoteDataSource, repository impl
                   (implements the domain interface, decides local-first + queue-for-sync)
  presentation/  — @riverpod code-gen notifiers + widgets, built only from design-system components
```

**Why this over V1's flat `data/` + `presentation/` per feature:** V1 repositories talk directly to Drift AND directly construct HTTP calls to the backend AND directly touch Firestore, all in one class (see `chat_page.dart`'s action-execution path, which reaches into four different repositories directly from the widget layer). Use cases give each business operation (e.g., "complete a task") ONE place to live, testable without Flutter, independent of whether the data source is local Drift, the new sync API, or (future) a different backend entirely.

**Sync engine — the single biggest mobile-side rebuild:**
V1's `SyncEngine` hand-writes five nearly-identical LWW diff/merge blocks against Firestore (577 lines, see KB §17). V2 replaces this entirely with a **generic cursor-sync client** (`SyncClient` in `core/sync/`) that:
1. Calls `GET /api/v2/sync?since=<cursor>` once, gets back all changed rows across all tables since the last cursor (server does the diffing — see §7).
2. Applies them to Drift generically via a `Syncable` mixin every entity implements (`toRow()`/`fromRow()` + `updatedAt`/`deletedAt`).
3. Pushes local pending mutations in one batched `POST /api/v2/sync` call, using the existing `PendingDeletes`-style outbox pattern generalized to *all* mutations (not just deletes) — an `OutboxEntry` table replaces ad-hoc `isSynced` booleans scattered across 5 tables.
4. Firestore is dropped from the client entirely once this ships — Postgres via the backend API is the one source of truth (§6). This also resolves the V1-hardening-era Firebase-Auth-vs-Firestore-uid complexity permanently: there is no more direct client→Firestore link to authenticate against.

**Offline & optimistic UI:** every mutation writes to Drift + the outbox synchronously (instant UI update), then syncs in the background; failed syncs retry with backoff and surface in the Notification Center rather than failing silently (V1 today: sync failures only `debugPrint`).

---

## 6. Database Redesign

**Decision: add Postgres as the relational source of truth. Keep Qdrant for vectors only (RAG chunks + memory embeddings). Firestore is retired.** This is the single highest-leverage change in the whole rebuild.

**Why:** In V1, the backend never sees a user's tasks/calendar/finance/health data — it lives only in the client's local Drift DB and (optionally, if the fragile Firebase-Auth path works) in Firestore. This makes server-side automation, cross-device consistency, backups, and analytics *structurally impossible* — not a bug to fix, a design limitation to remove. Every "AI Integration" and "Automation" objective in this rebuild's mandate requires the backend to actually hold the data.

**Why Postgres over staying Firestore-only:** relational integrity (foreign keys, check constraints), real transactions across tables (e.g., "complete task + log time entry" atomically), and mature migration tooling (Alembic) — none of which Firestore offers. Firestore's only advantage (client SDK can listen/write directly) is exactly the pattern we're removing in §5.

**Schema (V2, `aura_backend/infrastructure/db/models.py`):**
```
users(id uuid pk, email, display_name, photo_url, created_at, role)
tasks(id uuid pk, user_id fk, title, description_enc, priority, category,
      due_date, completed_at, google_task_id, created_at, updated_at, deleted_at)
calendar_events(id uuid pk, user_id fk, title, description_enc, start_time, end_time,
      all_day, google_event_id, created_at, updated_at, deleted_at)
finance_records(id uuid pk, user_id fk, type, amount, category, description_enc,
      occurred_at, created_at, updated_at, deleted_at)
health_records(id uuid pk, user_id fk, record_date, weight, water_ml, sleep_minutes,
      calories, exercise_minutes, created_at, updated_at, deleted_at)
memories(id uuid pk, user_id fk, fact_enc, qdrant_point_id, created_at, updated_at, deleted_at)
automations_log(id uuid pk, user_id fk, kind, input_ref, result_json, created_at)
insights(id uuid pk, user_id fk, kind, payload_json, surfaced_at, dismissed_at, created_at)
```
- Every syncable table gets `updated_at` (indexed, `(user_id, updated_at)` composite index) + `deleted_at` (soft delete) — this pair is *the entire sync protocol*: `WHERE user_id = ? AND updated_at > ?`. This single pattern replaces the `PendingDeletes` table, the five bespoke LWW comparators, and the Firestore subcollection mapping in one generic query, on both ends.
- `description_enc`/`fact_enc` columns store the same AES-256-GCM ciphertext format the client already produces (client-side field encryption is *kept* — server never sees plaintext task/memory text, only encrypted blobs it stores and returns; this is stronger than V1 where only the local DB was encrypted and Firestore held plaintext).
- Migrations via Alembic from day one (V1 backend has zero migration tooling).

---

## 7. API Redesign

**Decision: versioned `/api/v2/*` namespace, cursor-sync as the primary data API, SSE for chat instead of raw `text/plain`.**

- **`GET /api/v2/sync?since=<iso8601|null>`** → `{data: {tasks: [...], calendar_events: [...], ...}, cursor: <new iso8601>}`. One round trip for full delta sync across every domain table. Replaces 5+ REST resources and all direct Firestore access.
- **`POST /api/v2/sync`** → body is a batch of outbox mutations `[{table, op: upsert|delete, id, payload, client_updated_at}]`; server applies with last-write-wins on `updated_at`, returns per-item success/conflict.
- **`POST /api/v2/chat`** → Server-Sent Events (`text/event-stream`) with typed frames: `event: token`, `event: tool_call`, `event: tool_result`, `event: done`, `event: error`. Replaces V1's `[ACTIONS]` suffix-parsing hack — the client never regex-parses model output again, and every tool call the model wants to make is surfaced as a *proposal* the client can show a confirmation chip for before executing (fixes UX finding #5 — no more silent auto-creation of financial records from a model's text output).
- **`GET/POST /api/v2/insights`** — the new proactive-insights feed (§8).
- Standard envelope on every endpoint: `{"data": ..., "error": null, "meta": {...}}`; errors are `{"data": null, "error": {"code": "...", "message": "..."}}` — consistent machine-parseable error codes (V1 mixes raw `HTTPException(detail=str(e))` strings with no error codes).
- OpenAPI tags/examples filled in properly (FastAPI generates the schema automatically; V1 never populated descriptions/examples, so the free docs were useless).
- Idempotency: sync push mutations carry a client-generated UUID; replays are no-ops. Required once the backend is the authority and clients retry on flaky mobile networks.
- Legacy `/api/*` v1 endpoints (including the raw unauthenticated Ollama proxy passthroughs) are **not** exposed on the public v2 gateway going forward — Ollama proxying was always meant as an internal LAN convenience, not public API surface.

---

## 8. AI Integration V2 — new capability, not just cleanup

- **AURA Insights** (the flagship new feature): a Celery Beat job runs per-user on a schedule, reads recent Postgres domain events (task overdue, spending spike, sleep debt, low water intake, upcoming event conflict), and asks Gemini to rank the top 1-3 worth surfacing, written to `insights`. Client polls/subscribes and shows them at the top of **Today**. Every insight carries an explainability field: *"Based on 3 overdue tasks and your calendar being free until 3pm"* — directly answers the mandate's "Explainability" requirement and turns the existing-but-underused agent orchestration into something the user sees unprompted, not just when they open Chat.
- **Command-palette natural language search**: reuses the existing RAG pipeline, but now queries across tasks/notes/documents/memories in one embedding space (Qdrant multi-collection query) instead of only ingested documents.
- **Confirmation-gated tool calls** (§7) instead of silent `[ACTIONS]` execution — the AI still acts autonomously for low-risk reads, but any write (create expense, delete task) surfaces a one-tap confirm/undo chip, which the mandate's "trust" and "delight" goals require and V1 didn't have.
- **Auto-categorization**: the existing centroid-embedding technique (already used well for sentiment/intent routing in V1) is reused for expense category and task category suggestions — cheap, offline-capable, no new infra.
- **Smart scheduling**: when a task gets a due date, the backend checks `calendar_events` density and Gemini suggests 1-2 open slots — small feature, direct product-goal payoff ("saves users time").

---

## 9. Security V2

- **JWT + refresh token pair issued by the backend** after Google/Firebase verification, instead of the client re-verifying a Google ID token on every single request (V1 pattern). Short-lived access token (15 min) + rotating refresh token stored in secure storage; enables real session revocation (V1 cannot revoke a compromised Google ID token short of waiting for its own ~1hr expiry).
- **Device attestation — Android done, iOS deliberately left as a documented gap, not a guessed implementation**: Android Play Integrity was already properly implemented in V1's hardening pass — kept as-is. iOS's current check (`services/security.py`, `platform == "ios"` branch) is still a placeholder substring check. Phase 4 considered implementing real DeviceCheck/App Attest verification (CBOR-decode the attestation object, validate the certificate chain against Apple's App Attest root CA, check the nonce/RP-ID-hash/counter) but **deliberately did not**, for a specific reason: that verification can only be validated against a real Apple App Attest environment (a registered Team ID/App ID/bundle ID and an actual device-generated attestation object), none of which exist in this sandbox. Writing untestable cryptographic verification code — where a subtly wrong certificate-chain check or hash construction would fail silently and look identical to a correct one until it meets a real Apple payload — is a worse outcome than the current, honestly-labeled placeholder. Concrete steps for whoever has the Apple Developer entitlements to finish this: (1) register the app's Team ID + bundle ID for App Attest in App Store Connect, (2) capture a handful of real attestation objects from a test device, (3) implement CBOR-decode (`cbor2`, already added to requirements.txt for this purpose) + certificate chain validation against Apple's published App Attest root CA + nonce/counter checks, (4) validate against the captured real objects before deploying, matching the rigor this project applied to every other Phase 3/4 cryptographic/parsing change (unit-tested against real or realistic data, not just "it imports").
- **Field encryption kept — correction to this blueprint's original plan**: this section originally proposed "per-user envelope encryption (a KMS-held master key wraps a per-user data key)" on the server. Having now built §6/§7, that's wrong for this architecture: the server *never holds a decryption key at all* — `title_enc`/`description_enc`/`fact_enc` arrive as opaque AES-256-GCM ciphertext the client already produced, and Postgres just stores/returns the blob (verified in Phase 4 by `test_sync_encryption_boundary.py`, which asserts `_row_to_dict` passes ciphertext through byte-for-byte with zero processing). Introducing a server-side KMS-wrapped key to "manage" that data would *weaken* privacy, not strengthen it — it would require the server to gain plaintext access it currently never has. The real key-management surface is entirely client-side (already hardened in V1's pass: platform Keystore/Keychain, PBKDF2 fallback, integrity-checked on boot) and stays that way. Phase 4 instead added the regression guard above the flawed plan.
- **Secrets management — mostly a deployment-practice fix, not a code change**: `config.py` already reads every secret through `pydantic-settings` from environment variables (`DATABASE_URL`, `JWT_SECRET`, `GEMINI_API_KEY`, `QDRANT_API_KEY`, `FIREBASE_CREDENTIALS_JSON`, etc.) — that's already secrets-manager-agnostic, since any real secrets manager (cloud Secret Manager, Doppler, Infisical, Kubernetes secrets) injects values as environment variables at deploy time with zero application code changes required. The actual gap that let `.env`/`credentials.json`/`Deployment_info.txt` get committed (closed during the earlier hardening pass via `.gitignore`) was a repo-hygiene failure, not a missing abstraction layer. Remaining Phase 5 work here is operational: wire the chosen secrets manager into the CI/CD pipeline's deploy step, and rotate every credential that was exposed in git history before that pipeline goes live.
- **CI security gates**: Semgrep SAST + dependency vulnerability scanning (`pip-audit`, `dart pub outdated --mode=security` equivalent) on every PR, not manual review.

---

## 10. DevOps V2

- **CI/CD**: GitHub Actions — `lint → test → build` gate on every PR for both `aura_backend` and `aura_client`; merge to `main` auto-deploys backend.
- **Hosting**: move off Hugging Face Spaces (a demo-hosting platform, not a production target — no real autoscaling, no proper secrets manager, no zero-downtime deploys) to Cloud Run or Fly.io, both of which give real horizontal autoscaling (finally making the Redis-backed rate limiting/nonce work from the V1 hardening pass actually pay off across replicas) plus managed Postgres/Redis alongside.
- **Infra as code**: Terraform for Postgres/Redis/Qdrant/networking — no more hand-maintained `Deployment_info.txt` snapshots that silently go stale (this exact staleness was caught in the V1 audit).
- **Observability**: structured JSON logs + OpenTelemetry traces shipped to a real APM; Sentry (or equivalent) for client + server error tracking — V1's telemetry was a local JSONL file that's lost on every container restart.

---

## 11. Testing V2

- Backend: pytest + `testcontainers-python` spinning up real Postgres/Redis in CI (not mocks) for integration tests of the new sync endpoint; contract tests validated against the generated OpenAPI schema.
- Mobile: golden tests for every new design-system component (regression-proof the visual language); `integration_test` covering the 3 flagship flows (onboarding → first task capture, offline→online sync round-trip, chat tool-call confirmation).
- Both: security test suite carried forward from V1's hardening pass (`test_security.py` already covers rate limiting/attestation/RBAC well) extended to cover the new JWT/refresh flow.

---

## 12. Accessibility & Internationalization

- WCAG AA as a CI-checked property of the design token file (§4), not a checklist done once and forgotten.
- All strings moved to ARB files (`intl_en.arb` etc.) from day one of any V2 screen — no new screen ships with inline English strings. RTL tested via `Directionality` override in golden tests before any real Arabic/Hebrew locale is added.
- `Semantics` widgets required on every custom component in the design system (enforced by the component API itself needing a label parameter).

---

## 13. Phased Roadmap

| Phase | Scope | Status |
|---|---|---|
| **0** (this session) | This blueprint · V2 design tokens + light/dark theme · flagship "Today" screen rebuild · Postgres schema + Alembic · `/api/v2/sync` endpoint skeleton · Postgres added to docker-compose | **done** — see `lib/core/design_system/`, rebuilt `home_page.dart`, `infrastructure/db/`, `api/v2/sync.py`, `alembic/`, `docker-compose.yml`. Verified: WCAG-AA contrast test suite (16/16 pass), full Flutter test suite (24/24 pass), backend ORM mapper validation + full backend test suite, `main.py` import/route check. Not yet integration-tested against a live Postgres (none available in the dev sandbox) — do that first thing in Phase 1. |
| **1** | Full cursor-sync client on Flutter side (`SyncClient`, outbox table) wired to real repositories; retire Firestore + old `SyncEngine` internals | **done** — see `core/database/database.dart` (schema v7: `OutboxEntries`/`SyncMetadata`), `core/sync/sync_client.dart`, `core/sync/sync_payloads.dart`, all four domain repositories, rewritten `sync_engine.dart`. `cloud_firestore`/`firebase_auth` fully removed (grep-confirmed unused) along with the Firebase Auth session added in the V1 hardening pass, which existed only to satisfy Firestore's rules. Verified: full `flutter analyze` clean, 24/24 Flutter tests pass, backend ORM/import checks pass. **Known gaps**: UserMemories stays on the old Qdrant-backed `/api/memory/*` protocol (different data shape, deliberately deferred — see sync_client.dart's doc comment); Google Tasks/Calendar-imported rows aren't yet pushed into the outbox (only local user-initiated mutations are); not integration-tested against a live Postgres. |
| **2** | Full design-system rollout to every remaining screen; command palette; notification center | not started |
| **3** | AURA Insights engine (Celery Beat + event pipeline); SSE chat protocol replacing `[ACTIONS]` string-marker parsing | **done** — see `services/insights.py`, `api/v2/insights.py`, `tasks.py`'s beat task, `core/insights/insights_client.dart`, `api/v2/chat.py`, `services/sse_marker_splitter.py`, `core/ai/online_agents/sse_agent_client.dart`, rewired `chat_repository.dart`. **Correction to this blueprint's own UX audit (§2 finding #5)**: V1 already had a confirmation step — `chat_page.dart`'s `_buildPendingActionsCard()` shows an Approve/Reject card before any task/expense/event write executes. That finding was wrong; the real gap was transport fragility (a magic string marker embedded in a raw text stream, string-split by the client, silently dropping proposed actions on any parse failure). Phase 3 fixes the transport (typed SSE frames, server-side marker-boundary buffering, client falls back to the untouched v1 orchestrator path on any SSE failure) — the existing approve/reject UI needed zero changes. Verified: 10/10 new backend splitter unit tests (including the exact split-marker-across-chunks case), 8/8 new client SSE parser unit tests, 27/27 backend tests total, 32/32 Flutter tests total, full `flutter analyze` clean (0 new issues). **Known gaps**: Insights are rule-based only (no Gemini phrasing/ranking yet — deliberate, since the project's Gemini key was found suspended in Phase 0); not integration-tested against a live Postgres/Redis/deployed backend. |
| **4** | JWT/refresh auth; real iOS App Attest (blocked on Apple Developer entitlements); per-user envelope encryption; secrets manager migration | not started |
| **5** | CI/CD, Terraform, move off HF Spaces, observability stack, full test suite build-out, i18n rollout | **done** — see `.github/workflows/` (backend-ci, client-ci, deploy-backend), `infra/terraform/` (Cloud Run/SQL/Memorystore/Secret Manager/VPC/Workload Identity Federation), `infrastructure/observability.py` (structured JSON logs, Sentry, OpenTelemetry — all genuinely opt-in, verified to no-op cleanly), `test_sync_integration.py` (testcontainers-python, real Postgres), `l10n.yaml`/`lib/l10n/` (i18n pipeline + Today screen migrated as proof). **What's real vs. written-but-unrun**: the GitHub Actions workflows and Terraform were validated as far as this sandbox allows (YAML parses, HCL braces/references/variables all resolve, GCP naming constraints checked) but never executed — no CI runner, no `terraform` CLI, no cloud account, no Docker here. The observability code and i18n scaffold *did* run, in the real app/test process, and are verified the same way everything else this session was. The integration test is written correctly and skips itself cleanly (not silently, not erroring) when Docker is absent — confirmed by watching it actually skip in this sandbox; it's designed to run for real in `backend-ci.yml`, which does have Docker. |

Phases are sequenced by leverage: Phase 1 (real backend-owned data) is the prerequisite for nearly every later phase's value (Insights need data the backend can see; JWT/refresh needs the backend to own sessions meaningfully once it's the data authority). Design polish (Phase 2) is deliberately after the data-layer rebuild, not before, so it isn't reapplied twice.
