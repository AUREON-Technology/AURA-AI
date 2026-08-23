/// Rules for persistable Drift encryption keys, shared by native file backup
/// and the web secure-storage backup. Kept free of `dart:io`.
class EncryptionKeyPolicy {
  EncryptionKeyPolicy._();

  /// Hardcoded key from very old builds — never restore or persist it.
  static const rejectedLegacyKey = 'aura_ai_secure_db_encryption_key';

  static String? usableKey(String? raw) {
    if (raw == null) return null;
    final trimmed = raw.trim();
    if (trimmed.isEmpty || trimmed == rejectedLegacyKey) return null;
    return trimmed;
  }
}
