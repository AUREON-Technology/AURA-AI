# AURA AI Risk Assessment & Rollback Strategy

This document reviews system risks, rollback procedures, and disaster recovery.

## 1. Risk Matrix

| Risk | Impact | Probability | Mitigation |
| :--- | :--- | :--- | :--- |
| **Secure Key Loss** | High | Low | Implement PBKDF2 backup key derivation derived from unique system hardware identifiers and user secrets. |
| **Migration Failure** | High | Medium | Perform dry-run migrations in memory before updating SQLite/Drift databases. Keep automatic DB backups. |
| **Network Outage** | Medium | High | Offline-first architecture allows full local RAG, SQLite queries, and on-device `flutter_llama` inference. |
| **Attestation Fail** | High | Low | Gracefully downgrade clients to sandbox modes if device integrity checks fail due to OS modifications. |

## 2. Rollback Strategy
*   **Database Rollback**:
    *   If a Drift SQLite v6 migration fails on startup, the database automatically catches the exception, rolls back schema changes, and initializes the v5 layout from backup file.
*   **API Gateway Rollback**:
    *   Kubernetes green/blue deployments ensure immediate rollback to previous gateway containers if routing or attestation issues spike error rates.
*   **Firestore Rollback**:
    *   Firebase backup pipelines allow restoring the previous `/tasks` and `/calendar_events` paths in case of sync engine errors.
