import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// App-support file that mirrors the Drift DB encryption key.
///
/// Same directory family as the legacy `.aura_key` migration
/// ([getApplicationSupportDirectory] + a hidden file). A distinct name so
/// one-way cleanup of `.aura_key` cannot delete this backup.
class EncryptionKeyBackup {
  EncryptionKeyBackup._();

  static const fileName = '.aura_db_key';

  /// Hardcoded key from very old builds — never restore or persist it.
  static const _rejectedLegacyKey = 'aura_ai_secure_db_encryption_key';

  static File fileFor(Directory supportDir) =>
      File('${supportDir.path}/$fileName');

  static Future<Directory> supportDirectory() => getApplicationSupportDirectory();

  static Future<String?> readFrom(Directory supportDir) async {
    try {
      final file = fileFor(supportDir);
      if (!await file.exists()) return null;
      return usableKey(await file.readAsString());
    } catch (_) {
      return null;
    }
  }

  static Future<void> writeTo(Directory supportDir, String keyB64) async {
    await fileFor(supportDir).writeAsString(keyB64, flush: true);
  }

  /// Returns [raw] if it is a persistable key, otherwise null.
  static String? usableKey(String? raw) {
    if (raw == null) return null;
    final trimmed = raw.trim();
    if (trimmed.isEmpty || trimmed == _rejectedLegacyKey) return null;
    return trimmed;
  }
}
