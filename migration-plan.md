# AURA AI Migration Plan

This plan documents database schema modifications, Firestore path refactoring, and legacy data backfill/migration scripts.

## 1. Local SQLite/Drift DB Migration (v5 to v6)
Drift database migration schema updates columns `content` in `messages`, and `prompt` and `response` in `learning_logs` to use encrypted text mappings:
*   **Non-Destructive Migration**: In `database.dart`, we implement `MigrationStrategy` where:
    *   On upgrade from v5 to v6, we fetch existing plain text columns.
    *   Encrypt the raw values using `EncryptionService.encryptText()`.
    *   Update columns transparently and mark the DB version upgrade as completed.
*   **Transparent Cryptography**: Unencrypted database columns are automatically decrypted using a fallback check in `EncryptionService.decryptText()`. If a string lacks the `gcm:` prefix or fails verification, it returns the raw value, ensuring backward compatibility.

## 2. Firestore Schema Restructuring
Legacy Firestore paths (e.g., `/tasks/{id}`) are nested to enforce secure multi-tenant access controls:
*   **Target Hierarchy**:
    ```
    users/
      └── {uid}/
           ├── tasks/
           ├── memories/
           ├── calendar/
           └── finance/
    ```
*   **Firestore Rules Enforcement**:
    ```javascript
    rules_version = '2';
    service cloud.firestore {
      match /databases/{database}/documents {
        match /users/{userId}/{document=**} {
          allow read, write: if request.auth != null && request.auth.uid == userId;
        }
      }
    }
    ```

## 3. Backfill Script (`backfill_firestore.py`)
A batch migration script retrieves all un-nested legacy records and migrates them to the `/users/{uid}` subcollections.
