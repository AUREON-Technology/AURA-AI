# AURA AI Security Analysis & Threat Model

This document outlines the security architecture, threat model, cryptographic protocols, and Role-Based Access Control (RBAC) matrix for AURA AI.

## 1. Cryptographic Standard
*   **Symmetric Encryption**: AES-256-GCM.
*   **Format**: `gcm:<base64_iv>:<base64_ciphertext>` (contains ciphertext + GCM tag).
*   **Key Derivation (Fallback)**: PBKDF2-HMAC-SHA256 with 10,000 iterations using unique device hardware salt, user secret, and installation ID.
*   **Native Storage**:
    *   **Android**: EncryptedSharedPreferences (Keystore backed).
    *   **iOS/macOS**: Keychain Services.
    *   **Windows**: DPAPI (Data Protection API).

## 2. Role-Based Access Control (RBAC) Matrix

Authenticated API endpoints are protected using backend decorators enforcing a role hierarchy:

| Endpoint | Required Role | Purpose |
| :--- | :--- | :--- |
| `/api/admin/lora/train` | `Developer` | Trigger fine-tuning of local models |
| `/api/admin/dataset/export` | `Admin` | Export datasets for analysis |
| `/api/admin/telemetry` | `Moderator` | View telemetry logs |
| `/api/admin/system/config` | `SuperAdmin` | Modify runtime service routing configurations |
| `/api/memory/*` | `User` | Access personal RAG and memories |

## 3. Real Device Attestation & Threat Model
To prevent auth bypasses, API scraping, and botting, AURA AI implements real-device attestation:
*   **Android**: Google Play Integrity API.
*   **iOS**: DeviceCheck / App Attest.
*   **Backend Verification**:
    1.  Decrypt and verify the authenticity of the attestation payload using public keys.
    2.  Validate the signed nonce against Redis cache. Nonces are single-use with a 5-minute TTL.
    3.  Verify package name/bundle ID matches client credentials.
