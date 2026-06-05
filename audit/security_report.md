# AURA AI v2.0.0 Security Audit Report

## 1. Local Data Protection (Encryption Audit)
AURA AI secures user databases ( Drift SQLite files) by using column-level encryption via [encryption_service.dart](file:///e:/AURA%20AI/aura_client/lib/core/security/encryption_service.dart).

### 1.1 Cryptographic Algorithms
- **Cipher**: AES-256 in CBC (Cipher Block Chaining) mode.
- **Padding**: PKCS7 padding (applied automatically by the `encrypt` package).
- **IV (Initialization Vector)**: Generated per value using `IV.fromSecureRandom(16)` and prepended to the ciphertext separated by a colon (`iv:ciphertext`).

### 1.2 Key Storage Vulnerability (Critical Finding)
- **Primary Method**: The key is stored in the device's keychain/keystore via `flutter_secure_storage`.
- **Fallback 1**: If secure storage fails (common in Linux/Windows desktop environments or under certain OS constraints), the key is saved in **plaintext** inside `${getApplicationSupportDirectory()}/.aura_key`.
- **Fallback 2**: If the file system is also inaccessible, the system defaults to a **hardcoded static key** string: `aura_ai_secure_db_encryption_key`.
- **Risk**: A plaintext file on disk or a hardcoded static key enables potential extraction of encrypted DB content (e.g. decrypted tasks, finance records, chat histories) if the device is rooted/compromised.
- **Remediation**: Use platform-native keystores directly or enforce PBKDF2/scrypt key derivation from a user-defined passcode rather than using hardcoded fallback keys.

---

## 2. API Authentication & Token Protection
FastAPI routes are protected via JWT token verification in [security.py](file:///e:/AURA%20AI/aura_backend/services/security.py):

### 2.1 Verification Protocol
1. Resolves `Bearer <token>` from request headers.
2. Validates it first as a Google Sign-In ID Token via `google.oauth2.id_token.verify_oauth2_token`.
3. If that fails, attempts validation as a Firebase ID Token via `firebase_admin.auth.verify_id_token`.
4. If either succeeds, returns user metadata (`uid`, `email`).

### 2.2 Testing/Bypass Vulnerability
- In [security.py](file:///e:/AURA%20AI/aura_backend/services/security.py#L65-L76), if `settings.debug_mode` is true and no header is provided, a mock user payload is returned:
  ```python
  return {
      "uid": "mock_user_123",
      "email": "mock_user@aura.ai"
  }
  ```
- **Risk**: If `debug_mode` is accidentally enabled in production, the authentication layer is completely bypassed, permitting unauthorized API access.
- **Remediation**: Disable mock authentication entirely when `DEPLOYMENT_MODE` is set to `production`.

---

## 3. Rate Limiting and Payload Constraints
The FastAPI backend utilizes custom middlewares in [main.py](file:///e:/AURA%20AI/aura_backend/main.py#L31-L88):
- **RateLimiting**: Limits requests to 100 requests per minute per IP using an in-memory sliding window dictionary.
  - *Gap*: The dictionary stores requests in memory. During horizontal scaling (multiple instances in cloud environments), this rate limiter will not align across nodes. Moving to a Redis-backed token bucket is recommended.
- **Payload Limits**: Limits payloads to 5MB (`PayloadSizeLimitMiddleware`). This prevents DoS attacks from large files.

---

## 4. Firestore & Cloud Database Safety
- **RAG Context Partitioning**: Qdrant queries filter points using the `user_id` tag matching the verified token's `uid`. This ensures users cannot read other users' memories or indexed files.
- **Firestore Security Rules**: Ensure that write and read access to user collections is structured with strict user identity matches:
  ```javascript
  match /users/{userId}/{document=**} {
    allow read, write: if request.auth != null && request.auth.uid == userId;
  }
  ```
- **Gap**: Ensure Firebase rules are audited and enforced in the continuous integration pipeline.
