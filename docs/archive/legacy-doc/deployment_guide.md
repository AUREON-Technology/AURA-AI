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

`AppConfig.backendUrl` defaults to `http://localhost:8000`, so a plain `flutter run` talks to
the local gateway. You can also pass the same value explicitly:

```bash
cd aura_client
flutter run --dart-define-from-file=config/dev.json
```

Physical Android devices must not use localhost: from the repo root run `.\run_client_device.ps1` (or the VS Code "AURA Android device (production)" launch config).

Production builds must inject `https://$AURA_DOMAIN` (from `deploy/.env`) — never a bare IP.
With no owned domain that is `https://169.58.188.43.nip.io`. From the repo root:

```bash
./deploy_client.sh
# or:
flutter build windows --dart-define=BACKEND_URL=https://$AURA_DOMAIN
```
