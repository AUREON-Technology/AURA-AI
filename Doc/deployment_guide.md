# AURA AI v2.0.0 Deployment & Execution Guide

This document describes how to deploy the AURA AI backend server and build/run the Flutter client.

---

## 1. Backend Service Deployment

### 1.1 Environment Variables
Create a `.env` file inside `aura_backend/` with the following variables:
```ini
PORT=8000
GEMINI_API_KEY=AIzaSyYourKeyHere
QDRANT_HOST=https://your-qdrant-cluster.cloud.qdrant.io:6333
QDRANT_API_KEY=YourQdrantKeyHere
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3
DEBUG_MODE=False
```

### 1.2 Local Command Line Start
Install dependencies and run via Uvicorn:
```bash
cd aura_backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### 1.3 Container Deployment (Docker)
Build and run using docker-compose:
```bash
docker-compose up --build -d
```

---

## 2. Flutter Client Deployment

### 2.1 Configuration Variables
Compile-time configuration handles endpoint bindings. 

To run the application targeting a local developer backend:
```bash
cd aura_client
flutter run --dart-define=BACKEND_URL=http://localhost:8000
```

To build a production executable (e.g., Windows Desktop):
```bash
flutter build windows --dart-define=BACKEND_URL=https://your-production-server.com
```
