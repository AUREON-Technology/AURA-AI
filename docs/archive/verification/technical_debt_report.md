# AURA AI – Technical Debt Report

This document outlines and categorizes the technical debt found in the AURA AI ecosystem. Debt items are ranked by severity and annotated with estimation metrics to guide refactoring efforts.

---

## 1. Summary of Debt Rankings

| Severity | Count | Primary Impact | Action Required |
| :--- | :---: | :--- | :--- |
| **Critical** | 5 | Data security breaches, multi-platform crashes, sync failures. | Immediate refactoring before release. |
| **High** | 4 | Concurrency lockups, event-loop blockages, context bloat. | Address in next sprint. |
| **Medium** | 3 | Brittle extraction logic, slow telemetry, UI jank. | Schedule in technical debt backlog. |
| **Low** | 2 | Large asset bundles, configuration values hardcoded. | Refactor during routine maintenance. |

---

## 2. Detailed Technical Debt Registry

### 2.1 Critical Severity Debt

#### [CRIT-01] Non-Deterministic ID Generation (Python `hash()` and Dart `.hashCode`)
*   **File(s):**
    *   [database.dart](file:///e:/AURA%20AI/aura_client/lib/core/database/database.dart) (Long-term memory `saveFact` ID generation)
    *   [rag.py](file:///e:/AURA%20AI/aura_backend/services/rag.py) (Upsert point ID generation)
    *   [memory.py](file:///e:/AURA%20AI/aura_backend/services/memory.py) (Point ID generation)
*   **Description:** The application hashes text prompts and document paths using Python's built-in `hash()` function and Dart's `.hashCode`. These methods are non-deterministic and yield different values across program executions.
*   **Why It Matters:** When the server or mobile app restarts, existing records get assigned different IDs when queried or updated. This leads to duplicate records, broken delete actions, and bloated vector databases.
*   **Fix Effort:** Low (2 hours). Replace with SHA-256 or MD5 hashing to generate stable 64-bit integer IDs.
*   **Risk Level:** High.
*   **Impact:** High. Causes database inconsistency and duplicate storage.

#### [CRIT-02] Plaintext Cryptographic Key Fallbacks
*   **File(s):**
    *   [encryption_service.dart](file:///e:/AURA%20AI/aura_client/lib/core/security/encryption_service.dart)
*   **Description:** When `flutter_secure_storage` is unavailable (e.g. Linux/Windows platforms), the database encryption key is written in plaintext to a local file (`.aura_key`). If the filesystem also fails, it falls back to a hardcoded stable key string.
*   **Why It Matters:** Storing cryptographic keys in plaintext or hardcoding them in the binary defeats database encryption. A compromised device would allow attackers to read the user's encrypted local data.
*   **Fix Effort:** Medium (1-2 days). Implement Key Derivation (PBKDF2/scrypt) from a user passcode or use platform-specific secure keystores directly.
*   **Risk Level:** Critical.
*   **Impact:** Critical. Compromises local database security.

#### [CRIT-03] Broken root-level Firestore Sync Mappings
*   **File(s):**
    *   [sync_engine.dart](file:///e:/AURA%20AI/aura_client/lib/features/sync/sync_engine.dart)
    *   [firestore.rules](file:///e:/AURA%20AI/firestore.rules)
*   **Description:** The client sync engine queries collections at the root level of Firestore (e.g. `/tasks`, `/calendar_events`). However, the Firestore security rules only authorize access under user subcollections (`/users/{userId}/*`).
*   **Why It Matters:** The sync engine will fail on every execution with a `Permission Denied` exception.
*   **Fix Effort:** Medium (1 day). Update the client sync engine to write to user-specific subcollections (e.g., `users/{userId}/tasks`) and retrieve the authenticated User ID from Firebase/Google Auth state.
*   **Risk Level:** High.
*   **Impact:** Critical. Renders the cloud synchronization feature entirely broken.

#### [CRIT-04] Windows Platform GGUF Filename Path Crashes
*   **File(s):**
    *   [local_model_seeder.dart](file:///e:/AURA%20AI/aura_client/lib/core/ai/offline_agents/local_model_seeder.dart)
*   **Description:** The seeder creates a local model file on the device path using the string name `qwen2:1.5b`. Colons are reserved in Windows and cannot be used in filenames.
*   **Why It Matters:** The seeder throws a `FileSystemException` and crashes on Windows devices during initialization.
*   **Fix Effort:** Low (1 hour). Sanitize and replace colons with underscores in GGUF filenames (e.g. `qwen2_1.5b.gguf`).
*   **Risk Level:** High.
*   **Impact:** Critical. Prevents the app from running on Windows.

#### [CRIT-05] Missing Token Expiration Handlers (Cached Login Bypass)
*   **File(s):**
    *   [auth_provider.dart](file:///e:/AURA%20AI/aura_client/lib/features/auth/presentation/auth_provider.dart)
*   **Description:** When the user signs in using cached credentials (`signInCached()`), the app retrieves the stored `auth_token` directly from secure storage without verifying its expiration or initiating an OAuth refresh.
*   **Why It Matters:** Since ID tokens expire in 1 hour, cached user sessions will experience API failures (401 Unauthorized) when communicating with the FastAPI backend.
*   **Fix Effort:** Medium (1 day). Implement silent OAuth token refreshes using `GoogleSignIn.signInSilently()` on session restore.
*   **Risk Level:** High.
*   **Impact:** High. Leads to silent backend failures and bad user experience.

---

## 3. High Severity Debt

#### [HIGH-01] Synchronous AI Streams Block the Event Loop
*   **File(s):**
    *   [ai.py](file:///e:/AURA%20AI/aura_backend/services/ai.py)
    *   [agent.py](file:///e:/AURA%20AI/aura_backend/services/agent.py)
*   **Description:** The FastAPI endpoints invoke the synchronous method `chat.send_message_stream()` inside an `async def` function. This blocks the main event loop, preventing the server from handling concurrent client requests.
*   **Why It Matters:** The backend cannot scale. Servicing one user's chat stream will block other requests.
*   **Fix Effort:** Medium (1 day). Use the async `google-genai` client methods: `self.client.aio.chats.create` and `await chat.send_message_stream()`.
*   **Risk Level:** High.
*   **Impact:** High. Limits server scalability.

#### [HIGH-02] Duplicate RAG & Memory Double-Retrieval Loop
*   **File(s):**
    *   [agent_orchestrator.dart](file:///e:/AURA%20AI/aura_client/lib/core/ai/orchestrator/agent_orchestrator.dart)
    *   [agent.py](file:///e:/AURA%20AI/aura_backend/services/agent.py)
*   **Description:** The client retrieves user memories and RAG chunks, appends them to custom instructions, and sends them to the backend. The backend then queries the memory and RAG services again, appending the same context.
*   **Why It Matters:** This double-retrieval pattern bloats prompt sizes, increases API token costs, and adds latency.
*   **Fix Effort:** Medium (1 day). Establish a clear partition: either perform RAG/Memory retrieval on the client for local models, or let the backend handle it.
*   **Risk Level:** Low.
*   **Impact:** High. Increases token consumption and API costs.

#### [HIGH-03] Blocking Network Calls on Prompt Submission
*   **File(s):**
    *   [aura_memory.dart](file:///e:/AURA%20AI/aura_client/lib/core/ai/memory/aura_memory.dart)
    *   [memory_repository.dart](file:///e:/AURA%20AI/aura_client/lib/features/chat/data/memory_repository.dart)
*   **Description:** On every prompt submission, `queryMemories` calls `_repo.syncMemories()`, which performs a blocking GET network call to fetch all memories from the backend.
*   **Why It Matters:** This adds network latency to every prompt and breaks offline capabilities.
*   **Fix Effort:** Medium (1 day). Query the local SQLite Drift database directly for prompt matches and run sync operations in the background.
*   **Risk Level:** Medium.
*   **Impact:** High. Degrades response latency.

#### [HIGH-04] Insecure Plaintext Chat Databases
*   **File(s):**
    *   [database.dart](file:///e:/AURA%20AI/aura_client/lib/core/database/database.dart)
*   **Description:** Although memory tables are encrypted, the `Messages` and `LearningLogs` tables store prompts, responses, and chat transcripts in plaintext.
*   **Why It Matters:** Private chat logs are exposed if the local database file is extracted from the device.
*   **Fix Effort:** Low (3 hours). Apply `EncryptedTextConverter` mapping to `Messages.content`, `LearningLogs.prompt`, and `LearningLogs.response`.
*   **Risk Level:** High.
*   **Impact:** High. Potential privacy violation.

---

## 4. Medium Severity Debt

#### [MED-01] O(N) Telemetry File Operations
*   **File(s):**
    *   [main.py](file:///e:/AURA%20AI/aura_backend/main.py) (`log_telemetry`)
*   **Description:** The telemetry endpoint reads, parses, appends, and rewrites the entire `telemetry_logs.json` file on every request.
*   **Why It Matters:** Disk performance will degrade as logs accumulate, leading to request timeouts.
*   **Fix Effort:** Low (2 hours). Use a database or write logs to a JSON Lines (`.jsonl`) file in append mode.
*   **Risk Level:** Medium.
*   **Impact:** Medium. Performance degradation under load.

#### [MED-02] Cold-Start Sentiment Analyzer Latency
*   **File(s):**
    *   [sentiment.py](file:///e:/AURA%20AI/aura_backend/services/sentiment.py)
*   **Description:** The sentiment analyzer generates embeddings for 36 representative phrases on the first user query after startup.
*   **Why It Matters:** This causes a cold-start delay of 10-20 seconds for the first chat message.
*   **Fix Effort:** Medium (3 hours). Precompute and cache the sentiment centroids in a JSON file or compute them asynchronously on server startup.
*   **Risk Level:** Low.
*   **Impact:** Medium. Poor initial user experience.

#### [MED-03] Hardcoded Gemini Budget Constraints
*   **File(s):**
    *   [budget.py](file:///e:/AURA%20AI/aura_backend/services/budget.py)
*   **Description:** The budget counter limits requests to 2 Gemini API calls per query.
*   **Why It Matters:** Multi-step agent loops will crash when attempting to call multiple tools.
*   **Fix Effort:** Low (1 hour). Make `MAX_GEMINI_CALLS_PER_REQUEST` configurable and increase it for agents that require multi-step tool execution.
*   **Risk Level:** Low.
*   **Impact:** High. Breaks multi-step agent actions.

---

## 5. Low Severity Debt

#### [LOW-01] Bundled 900MB Asset
*   **File(s):**
    *   [pubspec.yaml](file:///e:/AURA%20AI/aura_client/pubspec.yaml)
*   **Description:** The 900MB Qwen-2 GGUF model is bundled directly inside the Flutter app assets.
*   **Why It Matters:** This violates app store size limits and increases installer sizes.
*   **Fix Effort:** Medium (1 day). Download the model on-demand post-install using `ModelDownloadManager`.
*   **Risk Level:** Low.
*   **Impact:** Medium. Deployment issues.

#### [LOW-02] Hardcoded OAuth IDs & API Credentials
*   **File(s):**
    *   [config.py](file:///e:/AURA%20AI/aura_backend/config.py)
    *   [auth_provider.dart](file:///e:/AURA%20AI/aura_client/lib/features/auth/presentation/auth_provider.dart)
*   **Description:** Google Client IDs are hardcoded in the codebase.
*   **Why It Matters:** Exposes client credentials in code repositories and makes environment management difficult.
*   **Fix Effort:** Low (2 hours). Use environment variables or inject them during build time.
*   **Risk Level:** Low.
*   **Impact:** Low. Configuration management issues.
