import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';

import 'encryption_key_policy.dart';

/// App-support file that mirrors the Drift DB encryption key.
///
/// Same directory family as the legacy `.aura_key` migration
/// ([getApplicationSupportDirectory] + a hidden file). A distinct name so
/// one-way cleanup of `.aura_key` cannot delete this backup.
class EncryptionKeyBackup {
  EncryptionKeyBackup._();

  static const fileName = '.aura_db_key';

  static const _backupExclusionChannel =
      MethodChannel('com.auraai.aura_client/backup_exclusion');

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

  /// Writes the key, then best-effort excludes the file from OS device
  /// backups (C3) — this is a plaintext resilience copy of the DB
  /// encryption key, and without exclusion it would be captured whole by
  /// a standard iCloud/Android/Time-Machine backup. Android's exclusion is
  /// declarative (AndroidManifest's dataExtractionRules/fullBackupContent,
  /// android/app/src/main/res/xml/); iOS/macOS require a per-file runtime
  /// call, made here via a platform channel (Runner/BackupExclusionPlugin).
  /// Failure to exclude never blocks the write itself — the key still has
  /// to be persisted for its primary resilience purpose either way.
  static Future<void> writeTo(Directory supportDir, String keyB64) async {
    final file = fileFor(supportDir);
    await file.writeAsString(keyB64, flush: true);
    await _excludeFromDeviceBackup(file);
  }

  static Future<void> _excludeFromDeviceBackup(File file) async {
    if (!Platform.isIOS && !Platform.isMacOS) return;
    try {
      await _backupExclusionChannel.invokeMethod('excludeFromBackup', {'path': file.path});
    } catch (e) {
      debugPrint('EncryptionKeyBackup: failed to exclude $fileName from device backup ($e).');
    }
  }

  /// Returns [raw] if it is a persistable key, otherwise null.
  static String? usableKey(String? raw) => EncryptionKeyPolicy.usableKey(raw);
}
