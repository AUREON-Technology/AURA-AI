# AURA AI v2.0.0 System Migration Guide

This document assists developers and system administrators migrating AURA AI instances from v1.x to the v2.0.0 release.

---

## 1. Database Schema Migrations (Drift)
Version v2.0.0 increments the schema version from `3` to `4` in [database.dart](file:///e:/AURA%20AI/aura_client/lib/core/database/database.dart).

- **New Tables**:
  - `LearningLogs`: Holds feedback signals (ratings and prompt corrections) for continuous model improvements.
  - `OfflineModelWeights`: Tracks paths and activations of local quantized weights.
- **Action Required**: The database migration strategy automatically handles upgrading existing SQLite databases. If you are developing locally, run:
  ```bash
  flutter pub run build_runner build --delete-conflicting-outputs
  ```

---

## 2. Google Console Scopes Expansion
To enable the research agent to index PDFs directly from the user's Google Drive, the OAuth 2.0 configuration scopes must be updated in the Google Cloud Console credentials dashboard.

1. **Required Scopes**:
   - `https://www.googleapis.com/auth/drive.readonly` (Read-only access to files)
   - `https://www.googleapis.com/auth/drive.metadata.readonly` (Read-only access to files metadata)
2. **Action Required**: If your project is in testing phase, add your testing email accounts to the OAuth Consent Screen test users list to prevent verification blocker blocks during authorization.

---

## 3. Dependency Modifications
- **Backend**: Add `pypdf` to `aura_backend/requirements.txt` to enable pdf ingestion pipelines.
- **Client**: Ensure all packages are synchronized:
  ```bash
  flutter pub get
  ```
