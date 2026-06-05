# AURA AI System Diagnosis & Log Analysis Report

This report documents the findings from auditing the backend execution logs and system screenshots of the AURA AI Android client.

---

## 1. System Status Summary

Based on the provided screenshots and backend logs, here is the operational status of the AURA AI ecosystem components:

| Component | Status | Details |
| :--- | :--- | :--- |
| **Authentication & Attestation** | **Healthy** | Google Play Integrity token verification succeeds on incoming requests. |
| **Qdrant Vector DB (RAG)** | **Healthy** | Connection and vector point queries/scrolls return HTTP `200 OK` successfully. |
| **Embeddings API** | **Healthy** | Batch embeds to `gemini-embedding-001` are successfully returned. |
| **Sentiment Analysis** | **Healthy** | Centroids initialization is successful, and offline text sentiment classification runs locally. |
| **Gemini Inference API** | **Degraded** | Quota exhaustion (HTTP `429 Too Many Requests`) is active. |
| **On-Device Inference** | **Broken Mismatch** | Database registers weights as operational, but generation fails due to file-path/routing ID mismatch. |

---

## 2. Root Cause Analysis

### Issue A: Gemini API Quota Exhaustion & Error Streaming
1. **The Log Pattern:**
   ```
   2026-06-05 13:55:14,616 - httpx - INFO - HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse "HTTP/1.1 429 Too Many Requests"
   ```
   The backend gets a `429 RESOURCE_EXHAUSTED` error from the Gemini API due to free tier limit exhaustion (limit is 20 requests per day per project for the model).
2. **The Budget Tracker Trigger:**
   The backend budget tracker attempts retries, exceeding the request limit of 2 calls, which throws `GeminiBudgetExceededException`.
3. **Stream Error Output:**
   The backend catches this exception and yields `[Error: AI quota exhausted. Please check Model Manager first.]` into the text stream.

---

### Issue B: Offline Fallback Model Mismatch (The Bug)
While the user enabled **Microsoft Phi-3 (3.8B)** in the Offline Model Manager (making GGUF weights locally available and bringing the local inference provider to an **Operational** state), the chat fails with:
`[Offline Inference Error: Model file not found on-device. Please download phi3 in the Model Manager first.]`

#### Detailed Breakdown of Mismatch:
1. **Model Download & DB Registration:**
   In [model_download_manager.dart](file:///e:/AURA%20AI/aura_client/lib/core/ai/offline_agents/model_download_manager.dart) and [model_manager_page.dart](file:///e:/AURA%20AI/aura_client/lib/features/models/presentation/model_manager_page.dart), models are defined using detailed IDs:
   - `id`: `"phi3:latest"`
   - `id`: `"llama3:8b"`
   - `id`: `"gemma2:2b"`
   - `id`: `"qwen2:1.5b"`
   
   Thus, downloading Microsoft Phi-3 writes weights to a local file named `phi3:latest` and adds it to the SQLite database with `id` / `modelName` as `"phi3:latest"`.

2. **Router Model Name Selection:**
   In [aura_ai_router.dart](file:///e:/AURA%20AI/aura_client/lib/core/ai/ai_router/aura_ai_router.dart), the router returns simplified model strings for offline execution:
   - `AIProvider.offlineFast` -> `"phi3"`
   - `AIProvider.offlineReasoning` -> `"mistral"`
   - `AIProvider.offlinePremium` -> `"llama3"`
   
   Auto-failover falls back to `"phi3"`.

3. **Inference Provider File Lookup Mismatch:**
   In [native_llama_provider.dart](file:///e:/AURA%20AI/aura_client/lib/core/ai/offline_agents/native_llama_provider.dart), the native provider checks for model files on disk using the string passed from the router:
   ```dart
   final modelFile = File("${modelsDir.path}/$model"); // Looks for File(".../models/phi3")
   ```
   Because the GGUF file on the device is named `phi3:latest` (or `llama3:8b`), `modelFile.exists()` returns `false`, causing the generation to abort with a "Model file not found" error.

---

## 3. Recommended Resolution

To fix the mismatch, we should update `NativeLlamaInferenceProvider.generateStream` to dynamically resolve the model GGUF filename by querying the active model from the local SQLite weights table, instead of relying on the raw router input parameter.

### Proposed Code Patch in `native_llama_provider.dart`

```diff
  @override
  Stream<String> generateStream({
    required String model,
    required List<Map<String, String>> messages,
    required String systemInstruction,
  }) async* {
    final appDir = await getApplicationDocumentsDirectory();
    
    // Ensure the models folder exists
    final modelsDir = Directory("${appDir.path}/models");
    if (!await modelsDir.exists()) {
      await modelsDir.create(recursive: true);
    }

-   final modelFile = File("${modelsDir.path}/$model");
-
-   if (!await modelFile.exists()) {
-     yield "\n[Offline Inference Error: Model file not found on-device. Please download $model in the Model Manager first.]";
-     return;
-   }
+   // Query the SQLite database to resolve the currently active, downloaded model name
+   final db = _ref.read(databaseProvider);
+   final activeModel = await (db.select(db.offlineModelWeights)
+         ..where((t) => t.isActive.equals(true) & t.isDownloaded.equals(true)))
+       .getSingleOrNull();
+
+   if (activeModel == null) {
+     yield "\n[Offline Inference Error: No active model selected or downloaded. Please configure this in the Model Manager.]";
+     return;
+   }
+
+   final modelFile = File("${modelsDir.path}/${activeModel.modelName}");
+
+   if (!await modelFile.exists()) {
+     yield "\n[Offline Inference Error: Model file '${activeModel.modelName}' not found on-device. Please download it in the Model Manager first.]";
+     return;
+   }

-   if (_loadedModelId != model) {
+   if (_loadedModelId != activeModel.modelName) {
      if (_isInitializing) {
        yield "\n[System: On-Device model initialization in progress. Please wait...]";
        return;
      }
      _isInitializing = true;
      try {
        final llama = FlutterLlama.instance;
        final config = LlamaConfig(
          modelPath: modelFile.path,
          nThreads: 4,
          nGpuLayers: -1, // Load as many layers to GPU as possible
          contextSize: 2048,
          batchSize: 512,
          useGpu: true,
        );
        final success = await llama.loadModel(config);
        if (!success) {
          throw Exception("Failed to load local GGUF model weights");
        }
-       _loadedModelId = model;
+       _loadedModelId = activeModel.modelName;
      } catch (e) {
        yield "\n[Offline Inference Error: Failed to initialize native LLM engine: $e]";
        return;
      } finally {
        _isInitializing = false;
      }
    }
```

This resolution guarantees that whatever model the user marks as active in the Model Manager is correctly resolved and loaded, completely eliminating ID mismatches.
