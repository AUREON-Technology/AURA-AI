# AURA AI v2.0.0 AI System Audit Report

## 1. AI Routing and Fallback Stack
The current backend orchestrator routes user requests through a multi-tier fallback architecture:
1. **Primary Model**: Streams via Google Gemini 2.5 Flash using the `google-genai` SDK.
2. **First Fallback**: If Gemini fails (e.g. 503 or 429), it switches to the local Ollama instance running the `llama3` model at `http://localhost:11434`.
3. **Second Fallback (Mock Mode)**: If Ollama is also offline, it checks if `settings.debug_mode` is active. If so, it yields developer mock stubs based on intent classification. If not in debug mode, it returns a structured JSON connection error message.

### 1.1 Intent Classification
- Intent is determined locally in `agent.py` using keyword-matching rules (rather than LLM-based classification to avoid latency and API costs):
  - `task_planner`: Matches keywords like *task, todo, calendar, event*.
  - `finance_advisor`: Matches keywords like *expense, budget, dollars, finance*.
  - `health_companion`: Matches keywords like *sleep, water, health, workout*.
  - `academic_coach`: Matches keywords like *study, university, code, programming*.
  - `research_agent`: Matches keywords like *search, drive, pdf, document, extract*.
  - `decision_agent`: Matches keywords like *decide, trade-off, pros and cons*.
  - Default: `orchestrator`.

---

## 2. Memory System (V1) Audit
The memory pipeline is managed by `memory_service` in `services/memory.py`:
- **Extraction**: Uses standard regular expressions against the user's last message to extract structured user statements (e.g. *my name is*, *I am studying*, *I want to*, *I am working on*).
  - *Audit Finding*: Regex-based extraction is brittle and misses complex contexts (e.g., "I'm starting a new python project next week").
- **Storage**: Converts facts to embeddings and upserts them to Qdrant with the tag `type: user_memory` and matching `user_id`.
- **Retrieval**: Performs a cosine similarity search on the prompt vector filtered by condition `type == user_memory` and injects results into the model's system prompt instructions.

---

## 3. Knowledge Base and RAG Ingestion
- Ingestion occurs via the `/api/rag/ingest` and `/api/rag/ingest_pdf` (custom file parsing) endpoints.
- Texts are chunked (1000 characters, 100 character overlap) and vectorized using:
  - `gemini-embedding-001` (768 dimensions) if online.
  - `SentenceTransformer("all-MiniLM-L6-v2")` (384 dimensions) or local Ollama embeddings if offline.
- **RAG/Memory Separation**: Since both document chunks and user memories are indexed in the same Qdrant collection, type metadata queries (`type: user_memory` vs general document metadata) are critical to partition the searches.

---

## 4. Multi-Agent Tool Binding
The `research_agent` currently implements native function calling tools bound to Gemini:
1. `search_google_drive(query)`: Queries metadata for PDF matches.
2. `ingest_google_drive_pdf(file_id, file_name)`: Downloads and parses PDF bytes into Qdrant.
3. `query_knowledge_base(query)`: Queries indexed chunks for target terms.

---

## 5. v2.0.0 Core Enhancements
- **Memory V2**: Upgrade regex-based memory extraction to an LLM-based parsing agent that generates facts from conversational histories asynchronously.
- **Client-side Routing**: Move network detection and fallback orchestration from the FastAPI backend directly to the Flutter client wrapper (`AuraAIRouter`) to keep the user experience seamless during local client-side network switches.
