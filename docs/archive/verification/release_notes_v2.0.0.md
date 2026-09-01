# AURA AI v2.0.0 Release Notes

We are thrilled to announce the production-ready release of **AURA AI v2.0.0**, featuring a hybrid online/offline architecture, continuous learning dataset compiler, and device safety validation.

---

## What's New in v2.0.0

### 1. Analytics Telemetry Dashboard
* **Real-time Metrics:** Capture request volumes, failovers, average latency, and ratings.
* **Graphical Charts:** Renders Line Charts for streaming latency trends and Pie Charts for active model distributions.
* **Drift Logs Integration:** Logs all system telemetry into SQLite for local querying.

### 2. Offline Model Download Manager
* **Direct Pulling:** Download weights (`gemma2:2b`, `phi3:latest`, `qwen2:1.5b`, `llama3:8b`) via Ollama daemon.
* **Download Controls:** Pause, resume, cancel, and track download speeds.
* **Model Selection:** Switch model configurations in real-time to toggle active local inference weights.

### 3. Continuous Learning & LoRA Pipelines
* **Feedback Loops:** Store correction logs for queries.
* **Dataset Export:** Build and download LoRA-ready training files formatted in Alpaca, ChatML, or OpenAI conversations formats.
* **Inference Statistics:** Track average ratings and the percentage of corrected prompts.

### 4. Centroid-based Sentiment Analysis
* **100% Offline Classification:** Evaluates query embeddings against representative centroids (Positive, Neutral, Negative, Frustrated, Urgent) via cosine similarity.
* **Dynamic Tone Adjustment:** Agent instruction templates adapt to match user state.

### 5. UI/UX Completion
* **System Control Center:** Rebuilt Settings Page, Profile Page, and Knowledge Center (RAG document ingest and vector query).
* **Glassmorphic Layouts:** Outfit typography, smooth animations, and skeleton loading support.

### 6. Production Security Hardening
* **Device Validation:** SafetyNet/Play Integrity on Android and App Attest on iOS verify client integrity.
* **Middlewares:** Payload size limits (5MB) and Rate limiting (100 req/min).
* **Firebase Rules Deployment:** Owner-only CRUD boundaries.

---

## Upgrade Guide

### Client Setup
1. Execute database generator:
   ```bash
   flutter pub run build_runner build --delete-conflicting-outputs
   ```
2. Launch client application:
   ```bash
   flutter run
   ```

### Backend Setup
1. Launch FastAPI backend:
   ```bash
   python main.py
   ```
2. Verify endpoints health:
   ```bash
   curl http://localhost:8000/health
   ```
