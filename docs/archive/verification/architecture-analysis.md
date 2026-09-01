# AURA AI Architecture Analysis

This document describes the architectural changes implemented to optimize the client-backend boundaries, double retrieval, and offline-first processing.

## 1. Single Responsibility Retrieval Architecture
To eliminate duplicate RAG & Memory retrieval overhead, we split retrieval duties between Online and Offline modes:

```
[User Message]
       │
       ▼
┌──────────────┐
│  Online?     ├───────Yes──────► [FastAPI Gateway]
└──────┬───────┘                        │
       │ No                             ▼
       ▼                        ┌──────────────┐
┌──────────────┐                │ RAG & Memory │
│ Client Local │                │ Context In   │
│ RAG & Llama  │                │ Backend Only │
└──────────────┘                └──────────────┘
```

*   **Online Mode**: The client forwards raw messages without querying local Drift DB or doing client-side RAG. The backend gateway fetches memories from Qdrant, applies structured memory extraction, retrieves relevant doc context, and streams the assembled prompt to Gemini.
*   **Offline Mode**: The client uses on-device Drift SQLite and local vector store querying to compile the RAG context locally and executes inference via `flutter_llama`.

## 2. Asynchronous Worker Queue Architecture
Heavy and high-latency processes are decoupled from request threads to avoid blocking execution:
*   **Worker Stack**: Celery, Redis broker, and PostgreSQL database.
*   **Offloaded Tasks**:
    1.  PDF parsing and structured text extraction.
    2.  Vector embedding generation.
    3.  Qdrant ingestion and indexing.
    4.  Large-scale file synchronization tasks.
