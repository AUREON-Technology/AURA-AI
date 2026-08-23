import 'package:encrypt/encrypt.dart' as encrypt;

import 'aura_secure_storage.dart';
import 'encryption_key_policy.dart';

const _backupKeyName = 'aura_db_encryption_key_backup';

Future<String?> readEncryptionBackupKey() async {
  try {
    return EncryptionKeyPolicy.usableKey(
      await auraSecureStorage.read(key: _backupKeyName),
    );
  } catch (_) {
    return null;
  }
}

Future<void> writeEncryptionBackupKey(String keyB64) async {
  await auraSecureStorage.write(key: _backupKeyName, value: keyB64);
}

Future<String?> readEncryptionLegacyKey() async => null;

Future<void> deleteEncryptionLegacyKey() async {}

Future<encrypt.Key?> deriveEncryptionFallbackKey() async {
  final key = encrypt.Key.fromSecureRandom(32);
  try {
    await writeEncryptionBackupKey(key.base64);
  } catch (_) {}
  return key;
}
