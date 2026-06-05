# AURA AI v2.0.0 Testing & Verification Guide

This document describes how to run automated unit, integration, and manual checks for the AURA AI platform.

---

## 1. Client Testing (Flutter)

### 1.1 Running Automated Unit Tests
To run all client-side unit tests, including the model routing selection tests:
```bash
cd aura_client
flutter test test/ai_router_test.dart
```

### 1.2 Running Widget UI Tests
To verify card renderings and button actions:
```bash
flutter test test/widget_test.dart
```

---

## 2. Backend Service Testing (Python)
Unit and integration tests reside in `aura_backend/`. Run them using `pytest` inside the virtual environment:
```bash
cd aura_backend
venv\Scripts\activate
pip install pytest
pytest
```

---

## 3. Manual Verification Guidelines

### 3.1 Network Switch Failover Check
1. Start the client and log in.
2. Select **Auto** inside the **AI Provider** settings dropdown.
3. Start a conversation thread.
4. Disable your host machine's Wi-Fi network interface.
5. Send a new message. Verify the client header shows `Offline Fallback` and successfully processes the stream chunk responses using the local Ollama backend.

### 3.2 Key Setup Test
1. Set an incorrect/expired local Gemini API key in the sidebar.
2. Disable the FastAPI backend server.
3. Send a message. Verify that the system throws a descriptive fallback alert card instead of failing silently.
