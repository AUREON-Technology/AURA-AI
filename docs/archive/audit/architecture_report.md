# AURA AI v2.0.0 Architectural Audit Report

## 1. Project Directory & Modular Structure
The AURA AI codebase is structured as a decoupled monorepo containing two main subsystems:
1. **aura_client**: A Flutter (Dart) mobile/desktop client shell.
2. **aura_backend**: A FastAPI (Python) service that handles AI orchestration, memory management, and RAG.

### 1.1 Client Directory Structure
The client follows a feature-based modular layout under clean architecture principles:
```text
aura_client/
├── lib/
│   ├── core/
│   │   ├── config/          # Global application configurations & constants
│   │   ├── database/        # Drift database schema (SQLite backend)
│   │   ├── routing/         # GoRouter navigation setup
│   │   ├── security/        # AES-256 local database encryption
│   │   └── theme/           # UI styling, color scheme, and typography tokens
│   ├── features/
│   │   ├── auth/            # Auth state and Google Sign-in flow
│   │   ├── automations/     # Email analysis and Google Workspace automations
│   │   ├── calendar/        # Google Calendar integration & repository
│   │   ├── chat/            # Chat interface & agent configuration
│   │   ├── finance/         # Transaction logs and fl_chart visualization
│   │   ├── health/          # Water, sleep, and workout trackers
│   │   ├── sync/            # Timestamp-based sync engine for cloud sync
│   │   └── tasks/           # Categories and checklists repository
│   └── main.dart            # Flutter entry point
```

### 1.2 Backend Directory Structure
The backend provides a stateless JSON/streaming API:
```text
aura_backend/
├── services/
│   ├── agent.py             # Agent orchestrator & system prompt definitions
│   ├── ai.py                # Gemini stream wrapper & Ollama fallback
│   ├── automation.py        # Email/GitHub analyzer templates
│   ├── budget.py            # Gemini API request quota tracker
│   ├── drive.py             # Google Drive PDF metadata and downloader
│   ├── memory.py            # Memory extraction & user-fact Qdrant indexing
│   ├── ollama.py            # Local Ollama chat & embedding API client
│   ├── rag.py               # Text chunking & Qdrant embedding generator
│   └── security.py          # JWT/OAuth2 validator with Google & Firebase verification
├── Dockerfile
├── docker-compose.yml
├── main.py                  # FastAPI server & route handlers
└── requirements.txt         # Dependencies list
```

---

## 2. Technical Stack Audit

| Layer | Technology | Current Implementation | Audit Findings & v2.0.0 Path |
| :--- | :--- | :--- | :--- |
| **Client Core** | Flutter (Dart) | Flutter SDK ^3.11.4 | Stable. Requires migration to responsive layouts. |
| **State Management**| Riverpod | `flutter_riverpod` ^3.3.1 | Clean. UI states are well isolated using providers. |
| **Navigation** | GoRouter | `go_router` ^17.3.0 | Declarative routes. Biometric auth integration needed. |
| **Local Database**  | Drift (SQLite) | `drift` ^2.33.0 | SQLite-backed with NativeDatabase. High performance. |
| **Secure Storage**  | Secure Storage | `flutter_secure_storage` | Stores DB keys & OAuth tokens. Compliant. |
| **Backend Core**    | FastAPI | Python 3.10+ & FastAPI | Lightweight async server. Stable. |
| **Vector Database** | Qdrant | Cloud + Local sqlite-backed | Segmented by `user_id`. Works online/offline. |
| **AI Models**       | Gemini + Ollama | Gemini 1.5/2.5 Flash, Ollama | Uses `google-genai` and `/api/chat` endpoints. |
| **Local ML**        | sentence-transformers | `SentenceTransformer` | Partial. Excluded from production `requirements.txt`. |

---

## 3. Database Architecture (Drift)
The client operates a local relational database (`AppDatabase` in [database.dart](file:///e:/AURA%20AI/aura_client/lib/core/database/database.dart)) with column-level AES-256 encryption.
Key tables:
- `Tasks`: Category, priority, completion state, synced status, and Google Task ID mappings.
- `CalendarEvents`: Start/end times, metadata, and Google Calendar sync references.
- `FinanceRecords`: Income/expense tracking with numerical fields.
- `HealthRecords`: Intakes, exercise durations, sleep minutes, and weight variables.
- `Messages`: Historical logs of the user/assistant sessions.
- `AgentConfigurations`: Persistent overriding system instructions.
- `UserMemories`: Encrypted semantic facts extracted locally.
- `PendingDeletes`: Deletion queue to sync deletes back to Firebase/Firestore.

---

## 4. Sync & Orchestration Mechanics
The system utilizes a hybrid sync engine that runs a background timer every 60 seconds.
- **Upstream Sync**: Changed items with `isSynced = false` are upserted into Firestore.
- **Downstream Sync**: Updates are retrieved using timestamp filters (`updatedAt > lastSyncTime`).
- **Conflict Resolution**: Uses Last-Write-Wins (LWW) resolution based on the `updatedAt` timestamps.
- **Deletions**: Records marked for deletion are written to `PendingDeletes`, processed upstream, and subsequently pruned locally.

---

## 5. Architectural Gaps & Upgrades (v2.0.0)
1. **Monolithic Chat Handler**: The `ChatRepository` directly routes request logic. It must delegate to a unified `AuraAIRouter` interface.
2. **Missing Local Inference Layer**: The system currently relies on the FastAPI backend hosting a local Ollama instance for offline usage. True offline hybrid operating capability requires a client-side AI/ML interface to manage offline state seamlessly.
3. **No Celery/Redis Queue**: Async tasks (like vector uploads, PDF indexing, and calendar syncs) run synchronously in FastAPI threads, creating latency spikes.
