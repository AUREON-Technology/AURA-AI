# AURA AI Testing Plan

This document outlines the testing strategy, test suites, and instructions for verifying AURA AI.

## 1. Test Coverage Goals
*   **Target Coverage**: `85%+` across all core layers.
*   **Security Tests**: 100% authorization path testing (RBAC, auth bypass attempts, device attestation token validation, encryption/decryption validation).

## 2. Test Execution Instructions

### Backend (Python/FastAPI)
Run backend tests using Python's `unittest` suite:
```bash
cd aura_backend
venv/Scripts/python.exe -m unittest test_agents.py
venv/Scripts/python.exe -m unittest test_security.py
```
*   `test_agents.py`: Evaluates Agent Orchestration, semantic classification intent routing, mock streaming agent tools, and budget limiters.
*   `test_security.py`: Verifies RBAC controls, admin path restrictions, rate limiters, token validation, and attestation nonces.

### Client (Flutter/Dart)
Run client unit and widget tests:
```bash
cd aura_client
flutter test
```
*   `ai_router_test.dart`: Validates network-aware agent routing (Gemini online, local Llama reasoning models, offline fast).
*   `encryption_test.dart`: Verifies native keystore saving, PBKDF2 fallback derivation, and AES-256-GCM encryption/decryption format.
*   `inference_verification_test.dart`: Checks on-device native Llama.cpp inference pipeline streaming using mock channels.
*   `widget_test.dart`: Validates UI bootstrap and login routing smoke test.
