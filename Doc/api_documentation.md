# AURA AI v2.0.0 Backend API Documentation

The FastAPI orchestrator provides endpoints for agent routing, memory management, and vector database ingestion.

---

## 1. Authentication
All endpoints require a JSON Web Token (JWT) sent in the HTTP `Authorization` header:
```http
Authorization: Bearer <token>
```
The token is validated against Google Sign-In or Firebase Authentication.

---

## 2. API Reference

### 2.1 Chat Stream
* **URL**: `/api/chat/stream`
* **Method**: `POST`
* **Request Body**:
```json
{
  "messages": [
    {"role": "user", "content": "Hello AURA"}
  ],
  "use_rag": true
}
```
* **Response**: A chunked text stream (`text/plain`).

---

### 2.2 Agent Execution
* **URL**: `/api/chat/agent`
* **Method**: `POST`
* **Request Body**:
```json
{
  "agent_id": "orchestrator",
  "messages": [
    {"role": "user", "content": "What are my coding tasks?"}
  ],
  "custom_instructions": {
    "task_planner": "You are a coding manager."
  },
  "use_rag": true,
  "google_access_token": "OAuth2AccessTokenString"
}
```
* **Response**: A chunked text stream. If tool execution or actions are triggered, they are appended at the end of the stream as `[ACTIONS] [{"type": "add_task", "params": {...}}]`.

---

### 2.3 RAG Ingestion
* **URL**: `/api/rag/ingest`
* **Method**: `POST`
* **Request Body**:
```json
{
  "doc_id": "doc_123",
  "content": "This is a document about Flutter clean architecture.",
  "metadata": {
    "category": "technical",
    "updated_at": "2026-06-05"
  }
}
```
* **Response**:
```json
{
  "status": "success",
  "message": "Document doc_123 ingested."
}
```

---

### 2.4 RAG Query
* **URL**: `/api/rag/query`
* **Method**: `POST`
* **Request Body**:
```json
{
  "query": "Flutter clean architecture",
  "limit": 3
}
```
* **Response**:
```json
{
  "results": [
    {
      "score": 0.89,
      "content": "Document text chunk...",
      "doc_id": "doc_123",
      "metadata": { ... }
    }
  ]
}
```
