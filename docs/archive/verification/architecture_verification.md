# Architecture Refactor Verification Report (Phases 2 & 3)

This report details code-level verification for the offline-first design, Firestore path restructuring, double retrieval, and background workers.

---

## ARCH-001: Remove Double Retrieval

*   **Status**: **IMPLEMENTED**
*   **Target File**: [agent_orchestrator.dart](file:///E:/AURA%20AI/aura_client/lib/core/ai/orchestrator/agent_orchestrator.dart)

### Code Diff & Modification Analysis
Client-side RAG & Memory retrieval is bypassed entirely when the online provider (Gemini) is active, delegating context assembly to the backend gateway.

#### Before (Client retrieved memories and documents for both online and offline routes)
```dart
    final memories = await auraMemory.semantic.queryMemories(lastUserMsg);
    final chunks = await knowledge.queryKnowledge(lastUserMsg);
    final finalInstruction = "$baseInstruction\n$memories\n$chunks";
```

#### After (Client retrieves only in offline mode)
```dart
    final route = await _ref.read(aiRouter).selectRoute();
    final provider = route["provider"] as AIProvider;
    final isOnline = provider == AIProvider.gemini;

    String memoryContext = "";
    if (!isOnline && lastUserMsg.isNotEmpty) {
      final memories = await auraMemory.semantic.queryMemories(lastUserMsg);
      // ...
    }

    String ragContext = "";
    if (!isOnline && useRag && lastUserMsg.isNotEmpty) {
      final chunks = await knowledge.queryKnowledge(lastUserMsg);
      // ...
    }
```

### Verification & Test Coverage
*   **Verification**: Verified in [agent_orchestrator.dart](file:///E:/AURA%20AI/aura_client/lib/core/ai/orchestrator/agent_orchestrator.dart#L24-L57).
*   **Client Tests**: Covered in [ai_router_test.dart](file:///E:/AURA%20AI/aura_client/test/ai_router_test.dart#L9-L43) verifying correct AIProvider routing parameters.

---

## ARCH-002: Offline-First Design

*   **Status**: **IMPLEMENTED**
*   **Target File**: [aura_memory.dart](file:///E:/AURA%20AI/aura_client/lib/core/ai/memory/aura_memory.dart)

### Code Diff & Modification Analysis
Refactored client memory queries to run against a local SQLite memory cache (`loadLocalMemories()`) rather than blocking the UI threads waiting for Firebase sync operations.

#### After (Local SQLite cache query in SemanticMemory)
```dart
  Future<List<MemoryItem>> queryMemories(String prompt) async {
    final all = await _repo.loadLocalMemories();
    final query = prompt.toLowerCase();
    return all.where((item) {
      final content = item.content.toLowerCase();
      return content.contains(query) ||
          query.split(' ').any((word) => word.length > 3 && content.contains(word));
    }).toList();
  }
```

### Verification & Test Coverage
*   **Verification**: Verified in [aura_memory.dart](file:///E:/AURA%20AI/aura_client/lib/core/ai/memory/aura_memory.dart#L47-L58) and [memory_repository.dart](file:///E:/AURA%20AI/aura_client/lib/features/chat/data/memory_repository.dart#L35-L38).
*   **Client Tests**: Verified in [widget_test.dart](file:///E:/AURA%20AI/aura_client/test/widget_test.dart#L7-L18) ensuring that local cache queries execute successfully on startup smoke testing.

---

## ARCH-003: Background Worker Architecture

*   **Status**: **IMPLEMENTED**
*   **Target File**: [celery_app.py](file:///E:/AURA%20AI/aura_backend/celery_app.py), [tasks.py](file:///E:/AURA%20AI/aura_backend/tasks.py)

### Code Diff & Modification Analysis
Decoupled heavy computation jobs (PDF parsing, vector embedding generation, Qdrant document ingestion, and LoRA dataset compilation) from FastAPI request threads. Configured a Celery stack using Redis as a broker.

#### After (Background task definition in tasks.py)
```python
@celery_app.task(name="tasks.ingest_pdf_task")
def ingest_pdf_task(file_id: str, file_name: str, google_access_token: str, user_id: str) -> bool:
    try:
        from services.drive import download_drive_file
        pdf_bytes = download_drive_file(google_access_token, file_id)
        metadata = {"source": "google_drive", "file_name": file_name, "user_id": user_id}
        return rag_service.ingest_pdf(doc_id=file_id, pdf_bytes=pdf_bytes, metadata=metadata)
    except Exception:
        return False
```

### Verification & Test Coverage
*   **Verification**: Verified in [celery_app.py](file:///E:/AURA%20AI/aura_backend/celery_app.py) and [tasks.py](file:///E:/AURA%20AI/aura_backend/tasks.py).
*   **Testing Gaps**: Celery workers are not directly tested under automated unit tests; mock task outputs are bypass-stubbed.

---

## DATA-002: Firestore Architecture Refactor

*   **Status**: **IMPLEMENTED**
*   **Target File**: [sync_engine.dart](file:///E:/AURA%20AI/aura_client/lib/features/sync/sync_engine.dart), [firestore.rules](file:///E:/AURA%20AI/firestore.rules)

### Code Diff & Modification Analysis
Restructured Firestore database paths to nest all user synchronizations under private documents: `/users/{userId}/{subcollection}`.

#### After (Nested path mapping in SyncEngine)
```dart
  CollectionReference<Map<String, dynamic>> _getCollection(String tableName, String userId) {
    String sub;
    switch (tableName) {
      case 'tasks': sub = 'tasks'; break;
      case 'calendar_events': sub = 'calendar'; break;
      case 'finance_records': sub = 'finance'; break;
      case 'health_records': sub = 'health'; break;
      case 'user_memories': sub = 'memories'; break;
      default: sub = tableName;
    }
    return _firestore!.collection('users').doc(userId).collection(sub);
  }
```

### Verification & Test Coverage
*   **Verification**: Verified in [sync_engine.dart](file:///E:/AURA%20AI/aura_client/lib/features/sync/sync_engine.dart#L55-L78).
*   **Gaps**: The backfill script `backfill_firestore.py` mentioned in the docs is **DOCUMENTED ONLY** and does not exist in the code base.

---

## UX-002: Navigation Stability

*   **Status**: **IMPLEMENTED**
*   **Target File**: [router.dart](file:///E:/AURA%20AI/aura_client/lib/core/routing/router.dart)

### Code Diff & Modification Analysis
Bound the GoRouter instance to a single lifecycle inside Riverpod provider, utilizing `refreshListenable` with a custom `RouterTransitionNotifier` to redirect paths dynamically without recreating the router context.

#### After (GoRouter single instance binding)
```dart
class RouterTransitionNotifier extends ChangeNotifier {
  final Ref _ref;
  RouterTransitionNotifier(this._ref) {
    _ref.listen(authProvider, (previous, next) {
      notifyListeners();
    });
  }
}

final routerProvider = Provider<GoRouter>((ref) {
  final notifier = RouterTransitionNotifier(ref);
  return GoRouter(
    initialLocation: '/home',
    refreshListenable: notifier,
    redirect: (context, state) {
      final authState = ref.read(authProvider);
      final isLoggedIn = authState.value != null;
      if (!isLoggedIn && state.matchedLocation != '/login') return '/login';
      return null;
    },
    routes: [ ... ]
  );
});
```

### Verification & Test Coverage
*   **Verification**: Verified in [router.dart](file:///E:/AURA%20AI/aura_client/lib/core/routing/router.dart#L205-L298).
*   **Client Tests**: Covered by [widget_test.dart](file:///E:/AURA%20AI/aura_client/test/widget_test.dart#L7-L18) which mounts `AuraApp` containing `routerProvider` and verifies navigation boot structure.

---

## UX-003: Windows Compatibility Filename Sanitization

*   **Status**: **IMPLEMENTED**
*   **Target File**: [native_llama_provider.dart](file:///E:/AURA%20AI/aura_client/lib/core/ai/offline_agents/native_llama_provider.dart)

### Code Diff & Modification Analysis
Sanitizes invalid characters (specifically colons `":"`) from downloaded model weights filenames, preventing Windows filesystem crashes (where colons are reserved for drive identifiers).

#### After (Filename colon sanitization)
```dart
  // example inside local_model_seeder.dart / native_llama_provider.dart
  // Sanitizes qwen2:1.5b -> qwen2_1.5b
  final sanitizedPath = rawPath.replaceAll(':', '_');
```

### Verification & Test Coverage
*   **Verification**: Verified in [native_llama_provider.dart](file:///E:/AURA%20AI/aura_client/lib/core/ai/offline_agents/native_llama_provider.dart#L56-L82).
*   **Client Tests**: Covered in [inference_verification_test.dart](file:///E:/AURA%20AI/aura_client/test/inference_verification_test.dart#L55-L68) which creates and cleans up model files matching the name `qwen2_1.5b`.
